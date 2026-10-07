import csv
import os
import re
from pathlib import Path
import logging
from collections import defaultdict
from odoo.tools.urls import urljoin as url_join
from odoo import fields, models, api, _
from odoo.modules.module import get_manifest
from odoo.exceptions import AccessError, UserError, ValidationError
from . import cap_api_call
from .prompts import image_prompts
from mistralai.client import Mistral
from mistralai.client.models import ToolFileChunk
from PIL import Image
import base64
import pdfkit
from odoo.tools import date_utils, format_date
from dateutil.relativedelta import relativedelta
from itertools import product
from functools import partial
from datetime import datetime, time, timedelta
from odoo.fields import Date, Datetime
import random
import time as sleep_time
from odoo.http import request
from requests.exceptions import RequestException
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import cairosvg

_logger = logging.getLogger(__name__)
_SINGLE_QUOTED_FIELD_RE = re.compile(r"(^|,)\s*'((?:[^'\\]|\\.|\'{2})*)'\s*(?=,|$)")
_FIX_COMMAS_RE = re.compile(r'"[^"]*"|(,\s+)')
_CSV_BLOCK_RE = re.compile(r"```csv\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_HTML_BLOCK_RE = re.compile(r"```html(.+?)```", re.DOTALL)
_FRENCH_COUNTRIES = frozenset({'France', 'Canada', 'Luxembourg', 'Madagascar'})
_SLUG_RE = re.compile(r"[^a-zA-Z0-9 ]")
_FRENCH_LANG_CODE = "fr_FR"

MISTRAL_SMALL = "mistral-small-latest"
MISTRAL_MEDIUM = "mistral-medium-latest"


class ImageAgent:
    def __init__(self, api_key: str):
        self.client = Mistral(api_key)
        self.image_agent = None

    def gen_image(self, prompt: str):
        response = self.client.beta.conversations.start(
            agent_id=self.image_agent.id, inputs=prompt
        )
        for _, chunk in enumerate(response.outputs[-1].content):
            if isinstance(chunk, ToolFileChunk):
                file = self.client.files.download(file_id=chunk.file_id)
                return file.read()
        return None

    def save_image(self, prompt: str, path_name: str):
        file_byte = self.gen_image(prompt)
        if file_byte:
            with open(path_name, "wb") as image_file:
                image_file.write(file_byte)
        else:
            _logger.warning("No image generated for the prompt: ", prompt)

    def create_agent(self):
        if not self.image_agent:
            self.image_agent = self.client.beta.agents.create(
            model=MISTRAL_MEDIUM,
            name="Image generation API",
            description="Agent used to create images for the Demo Data module",
            instructions="Use the image generation tool to create the images.",
            tools=[{"type": "image_generation"}],
            completion_args={"temperature": 0.3},
            )

    def get_agent(self):
        if not self.image_agent:
            agent_list = self.client.beta.agents.list()
            self.image_agent = next(
                (agent for agent in agent_list if agent.name == "Image generation API"),
                None)

            if self.image_agent is None:
                self.create_agent()


class ModuleLoadAction(models.Model):
    _name = "module.load.action"
    _description = "An action to generate demo data for a module"

    file_to_load = fields.Char()
    name = fields.Char(string="Action's name", required=True)
    state = fields.Selection(
        selection=[
            ("ok", "OK"),
            ("processing", "Processing"),
            ("in_line", "In Line"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ]
    )
    nb_tries = fields.Integer(string="Number of tries", readonly=True)
    prompt = fields.Text(readonly=False)
    logs = fields.Text(string="Errors", readonly=True)
    demo_data_id = fields.Many2one("demo.data", string="Model")
    dependent_module = fields.Char(string="Module", required=True)

    def retry(self):
        self.nb_tries += 1
        self.generate_data(self.demo_data_id.get_values_dict(), self.demo_data_id.api_key_id.name)
        self.load_data()
        self.state = "ok"

    @api.onchange("prompt")
    def _on_change_prompt(self):
        self.state = "in_line"
        self.demo_data_id.generation_in_progress = True

    def _normalize_llm_csv(self, csv_text: str) -> str:
        """
        Normalize LLM-generated CSV: converts single-quoted fields to double-quoted.
        """
        def repl(m):
            prefix = m.group(1)
            value = m.group(2).replace("''", "\x00").replace('"', '""').replace("\x00", "'")
            return f'{prefix}"{value}"'

        return '\n'.join(
            _SINGLE_QUOTED_FIELD_RE.sub(repl, line)
            for line in csv_text.splitlines()
        )

    def _fix_csv_commas(self, csv_text: str) -> str:
        """
        Removes spaces after comma field separators without touching
        spaces inside double-quoted fields (e.g. "New York, NY" is preserved).
        """
        def repl(m):
            return m.group(0) if m.group(1) is None else ','

        return '\n'.join(_FIX_COMMAS_RE.sub(repl, line) for line in csv_text.splitlines()).strip()

    def generate_data(self, dict_fill_in: dict, api_key: str):
        if not self.file_to_load or not self.prompt:
            _logger.info("No generation for this action")
            return
        _logger.info("Starting generation for", self.name)
        for key, value in dict_fill_in.items():
            self.prompt = self.prompt.replace(key, value)

        if "account.asset" in self.prompt:
            accounts = self.env["account.account"].search_read(
                [("company_ids", "parent_of", self.demo_data_id.company_id.id),
                 ('account_type', 'in', ['asset_current', 'asset_non_current', 'asset_fixed', 'expense'])],
                ["name", "account_type"],
            )
            self.prompt = self.prompt + str(accounts)

        result = cap_api_call.mistral_api_call(MISTRAL_SMALL, self.prompt, api_key, self.demo_data_id.lang)
        text = result.choices[0].message.content
        # print("text",text)
        files_to_load = [f.strip() for f in self.file_to_load.split(";")]

        is_csv = any(f.lower().endswith(".csv") for f in files_to_load)
        base_path = Path(self.demo_data_id.data_path)

        if not is_csv:
            for file_name in files_to_load:
                (base_path / file_name).write_text(text, encoding="utf-8-sig")
            return

        matches = _CSV_BLOCK_RE.findall(text)

        if not matches:
            raise ValueError("No CSV blocks found in LLM response")

        if len(matches) < len(files_to_load):
            raise ValueError(f"CSV blocks mismatch: expected {len(files_to_load)}, got {len(matches)}")

        for file_name, csv_content in zip(files_to_load, matches):
            csv_content = re.sub(r"^```csv\s*", "", csv_content.strip(), flags=re.IGNORECASE)
            cleaned_csv = self._normalize_llm_csv(csv_content)
            cleaned_csv = self._fix_csv_commas(cleaned_csv)
            (base_path / file_name).write_text(cleaned_csv, encoding="utf-8-sig")
        _logger.info("Finished generation for ", self.name)

    def download_logo(self, url, save_folder):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            logo_url = None
            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                logo_url = og_image["content"]
            if not logo_url:
                for img in soup.find_all("img"):
                    alt = img.get("alt", "").lower()
                    cls = " ".join(img.get("class", [])).lower()
                    if "logo" in alt or "logo" in cls:
                        logo_url = img.get("src")
                        break
            if not logo_url:
                raise ValueError(f"No logo found at URL: {url}")
            logo_url = urljoin(url, logo_url)
            img_response = requests.get(logo_url, headers=headers, timeout=10)
            img_response.raise_for_status()
            img_data = img_response.content

            if logo_url.split("?")[0].lower().endswith(".svg"):
                img_data = cairosvg.svg2png(bytestring=img_data)

            os.makedirs(save_folder, exist_ok=True)
            file_name = os.path.join(save_folder, logo_url.split("/")[-1].split("?")[0])
            file_name = os.path.splitext(file_name)[0] + ".png"  # always save as .png
            with open(file_name, "wb") as f:
                f.write(img_data)
            return img_data, None
        except Exception as e:
            return None, str(e)

    def _load_general_information(self):
        """
        Useful to prevent 'General Information' action from crashing
        """
        current_user = self.env.user
        if not current_user:
            return

        demo = self.demo_data_id
        email = f"{demo.name.lower()}@test.com"

        vals = {"email": email, "phone": "+1234567890"}
        if "work_email" in current_user._fields:
            vals["work_email"] = email
            vals["work_phone"] = "+1234567890"
        current_user.write(vals)

        img_data, error = self.download_logo(demo.website_url, Path(demo.data_path))
        if error:
            self.logs = (self.logs or "") + f"{error}"
        else:
            logo_b64 = base64.b64encode(img_data)
            demo.logo = logo_b64
            if demo.company_id:
                demo.company_id.logo = logo_b64


    def _load_background(self):
        demo = self.demo_data_id
        data_path = Path(demo.data_path)
        prompt = (data_path / "background_image.txt").read_text(encoding="utf-8-sig")
        image_agent = ImageAgent(demo.api_key_id.name)
        image_agent.get_agent()
        image_bytes = image_agent.gen_image(prompt)
        if not image_bytes:
            _logger.warning("No background image generated")
            return
        (data_path / "background_image.jpg").write_bytes(image_bytes)
        demo.company_id.background_image = base64.b64encode(image_bytes)

    def _load_products(self):
        demo = self.demo_data_id
        data_path = Path(demo.data_path)
        is_stock = (self.env["ir.module.module"].search([("name", "=", "stock")], limit=1).state in
                    ["installed", "to_remove", "to_upgrade"] and demo.stock)

        has_invoice_policy = "invoice_policy" in self.env["product.product"]._fields
        company_restricted = demo.bool_company_restricted
        company_id = demo.company_id.id
        ProductTemplate = self.env["product.template"].with_company(demo.company_id)

        def _load_product(file_name):
            with open(data_path / file_name, mode="r", encoding="utf-8-sig") as csv_file:
                for row in csv.DictReader(csv_file):
                    vals = {"name": row.get("name"),
                            "default_code": row.get("default_code"),
                            "list_price": float(row.get("list_price", "0").replace(",", "")),
                            "type": row.get("type"),
                            "standard_price": row.get("standard_price"),}

                    if file_name == "customer_products.csv":
                        vals["sale_ok"] = True
                    if "invoice_policy" in self.env["product.product"]._fields:
                        vals["invoice_policy"] = "delivery"
                    if "volume" in self.env["product.template"]._fields:
                        vals["volume"] = row.get("volume")
                    if company_restricted:
                        vals["company_id"] = company_id
                    if row.get("type") == "consu" and is_stock:
                        vals["is_storable"] = True
                    ProductTemplate.create(vals)

        _load_product("customer_products.csv")
        _load_product("supplier_products.csv")

    def _partner_lang(self, country, overwrite=True):
        if country not in _FRENCH_COUNTRIES:
            return "en_US"

        lang = self.env['res.lang'].with_context(active_test=False).search([("code", "=", _FRENCH_LANG_CODE)], limit=1)
        if not lang:
            return "en_US"

        if not lang.active:
            lang.action_unarchive()
            self.env['ir.module.module'].search([('state', '=', 'installed')])._update_translations(_FRENCH_LANG_CODE,
                                                                                                    overwrite)

        return _FRENCH_LANG_CODE

    def _load_partners(self):
        demo = self.demo_data_id
        data_path = Path(demo.data_path)
        company_restricted = demo.bool_company_restricted
        company_id = demo.company_id.id
        Partner = self.env["res.partner"].with_company(demo.company_id)
        user = self.env.user

        countries = {r["name"]: r["id"] for r in self.env["res.country"].search_read([], ["name", "id"])}
        states = {(r["country_id"][0], r["name"]): r["id"] for r in
                  self.env["res.country.state"].search_read([], ["name", "id", "country_id"])}

        def _build_vals(row, rank_field, tag):
            country_name = row.get("country", "")
            country_id = countries.get(country_name, False)
            state_id = states.get((country_id, row.get("state", "")), False) if country_id else False
            vals = {
                "name": row.get("name"),
                "email": row.get("email"),
                "phone": row.get("phone"),
                "street2": row.get('street', ''),
                "city": row.get("city"),
                "zip": row.get("zip"),
                "website": row.get("website"),
                rank_field: 1,
                "is_company": True,
                "category_id": tag,
                "country_id": country_id,
                "state_id": state_id,
                "lang": self._partner_lang(country=country_name, overwrite=True),
                "followup_responsible_id": user.id,
            }
            if company_restricted:
                vals["company_id"] = company_id
            return vals

        def _geo_localize(partner):
            sleep_time.sleep(4)
            backoff = 6
            for attempt in range(3):
                try:
                    partner.geo_localize()
                    return
                except Exception:
                    if attempt < 2:
                        sleep_time.sleep(backoff)
                        backoff *= 2

        customer_tag = self.env["res.partner.category"].with_company(demo.company_id).create({"name": "customer"})
        with open(data_path / "customers.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                partner = Partner.create(_build_vals(row, "customer_rank", customer_tag))
                _geo_localize(partner)

        supplier_tag = self.env["res.partner.category"].with_company(demo.company_id).create({"name": "supplier"})
        with open(data_path / "suppliers.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                partner = Partner.create(_build_vals(row, "supplier_rank", supplier_tag))
                _geo_localize(partner)

        log_sets = defaultdict(list)
        with open(data_path / "log_chatter.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                log_sets[int(row["set"])].append(row)
        for logs in log_sets.values():
            logs.sort(key=lambda x: int(x["sequence"]))

        contacts = Partner.search(
            ["|", ("category_id.name", "=", "supplier"), ("category_id.name", "=", "customer")]
        )
        now = demo.demo_date
        three_months_ago = now - timedelta(days=90)
        total_seconds = int((now - three_months_ago).total_seconds())
        log_set_keys = list(log_sets.keys())
        user_partner = user.partner_id
        user_partner_id = user_partner.id
        user_partner_email = user_partner.email

        for contact in contacts:
            thread_logs = log_sets[random.choice(log_set_keys)]
            random_dates = sorted([
                three_months_ago + timedelta(seconds=random.randint(0, total_seconds))
                for _ in range(len(thread_logs))
            ])
            for log, msg_date in zip(thread_logs, random_dates):
                values = {
                    "subject": log.get("subject"),
                    "model": "res.partner",
                    "res_id": contact.id,
                    "body": log.get("body"),
                    "message_type": "comment",
                    "date": msg_date,
                }
                if log.get("from") in {"customer", "supplier"}:
                    values.update({
                        "author_id": contact.id,
                        "email_from": f'"{contact.name}" <{contact.email}>',
                        "reply_to": f'"{contact.name}" <{contact.email}>',
                    })
                else:
                    values.update({
                        "author_id": user_partner_id,
                        "email_from": user_partner_email,
                        "reply_to": user_partner_email,
                    })
                self.env["mail.message"].create(values)

    def _load_child_contacts(self):
        demo = self.demo_data_id
        Partner = self.env["res.partner"].with_company(demo.company_id)
        company_restricted = demo.bool_company_restricted
        company_id = demo.company_id.id

        company_partner_ids = [r["partner_id"][0] for r in
                               self.env["res.company"].sudo().search_read([], ["partner_id"])]

        excluded_ids = company_partner_ids + [self.env["res.users"].browse(2).partner_id.id]

        parents = Partner.search([("is_company", "=", True), ("id", "not in", excluded_ids)])
        if not parents:
            return

        parent_data = {r["id"]: r for r in Partner.search_read([("id", "in", parents.ids)], ["name", "email"])}

        parent_email_domains = {}
        for pid, data in parent_data.items():
            parent_email = data.get("email") or ""
            if "@" in parent_email:
                domain = parent_email.split("@")[-1].strip().lower()
            else:
                domain = f"{_SLUG_RE.sub('', data.get('name', '')).lower().replace(' ', '')}.com"
            parent_email_domains[pid] = domain

        available_parents = parents.ids[:]
        random.shuffle(available_parents)
        parent_remaining = {pid: 2 for pid in available_parents}

        with open(Path(demo.data_path) / "child_contacts.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                if not available_parents:
                    break

                selected_parent_id = random.choice(available_parents)
                parent_remaining[selected_parent_id] -= 1
                if parent_remaining[selected_parent_id] == 0:
                    available_parents.remove(selected_parent_id)

                child_name = str(row.get("name", "")).strip()
                local_part = _SLUG_RE.sub("", child_name).lower().replace(" ", ".") if child_name else ""
                child_email = f"{local_part}@{parent_email_domains[selected_parent_id]}" if local_part else ""

                values = {
                    "name": row.get("name"),
                    "email": child_email,
                    "phone": row.get("phone"),
                    "parent_id": selected_parent_id,
                }
                if company_restricted:
                    values["company_id"] = company_id

                Partner.create(values)

    def _load_analytic_tags(self):
        settings = self.env['res.config.settings'].create({
            'group_analytic_accounting': True,
            'module_account_budget': True,
        })
        settings.execute()

        demo = self.demo_data_id
        company = demo.company_id
        AnalyticPlan = self.env["account.analytic.plan"].with_company(company)
        AnalyticAccount = self.env["account.analytic.account"].with_company(company)
        Budget = self.env["budget.analytic"].with_company(company)

        self.env["account.analytic.line"].with_company(company).search([]).unlink()
        existing_budgets = Budget.search([])
        existing_budgets.action_budget_draft()
        existing_budgets.unlink()

        plan_id = AnalyticPlan.search([("name", "ilike", "Internal")], limit=1)
        if not plan_id:
            plan_id = AnalyticPlan.create({
                "name": "Internal",
                "default_applicability": "optional",
            })

        account_vals = []
        with open(Path(demo.data_path) / "analytic_accounts.csv", "r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                account_vals.append({
                    "name": row.get("name"),
                    "code": row.get("code"),
                    "plan_id": plan_id.id,
                })
        AnalyticAccount.create(account_vals)

        account_dict = {plan_id._column_name(): plan_id.account_ids.ids}

        account_codes = {r["id"]: r["code"] for r in AnalyticAccount.search_read([], ["id", "code"])}

        budget_lines = []
        for combination in product(*account_dict.values()):
            line = dict(zip(account_dict.keys(), combination))
            x_plan2_id = line.get("x_plan2_id")
            if x_plan2_id and "PO" in (account_codes.get(x_plan2_id) or ""):
                line["budget_amount"] = random.randint(30, 80) * 1000
            budget_lines.append((0, 0, line))

        fmt_year = partial(format_date, self.env, date_format="yyyy")
        step = relativedelta(years=1)
        current_year = fields.Date.today().year
        period_start = fields.Date.from_string(f"{current_year}-01-01")
        period_end = fields.Date.from_string(f"{current_year}-12-31")
        budget_vals = []
        for period_start in date_utils.date_range(period_start, period_end, step):
            period_end = period_start + step - relativedelta(days=1)
            budget_vals.append({
                "name": _("Budget %s", fmt_year(period_start)),
                "date_from": period_start,
                "date_to": period_end,
                "budget_type": "expense",
                "budget_line_ids": budget_lines,
            })
        Budget.create(budget_vals).action_budget_confirm()


    def _load_bills(self):
        demo = self.demo_data_id
        data_path = Path(demo.data_path)
        company = demo.company_id
        company_id = company.id

        _logger.info("fiscal localisation applied:", company.chart_template)

        if self.env.ref("account.group_account_basic") not in self.env.user.group_ids:
            self.env.user.group_ids += self.env.ref("account.group_account_basic")

        Move = self.env["account.move"].with_company(company)
        MoveLine = self.env["account.move.line"].with_company(company)
        Partner = self.env["res.partner"].with_company(company)

        posted_moves = Move.search([("company_id", "=", company_id), ("state", "=", "posted")])
        posted_moves.button_draft()
        Move.search([("company_id", "=", company_id)]).unlink()
        self.env["account_followup.followup.line"].with_company(company).search([]).unlink()

        partners = {r["name"].lower(): r["id"] for r in Partner.search_read([], ["name", "id"])}
        products = {r["name"].lower(): r["id"] for r in
                    self.env["product.product"].with_company(company).search_read([], ["name", "id"])}

        po_account_ids = []
        so_account_ids = []
        if demo.accountant:
            AnalyticAccount = self.env["account.analytic.account"].with_company(company)
            po_account_ids = AnalyticAccount.search([("code", "ilike", "PO")]).ids
            so_account_ids = AnalyticAccount.search([("code", "ilike", "SO")]).ids

        def _analytic_distribution(move_type):
            if move_type == "in_invoice" and po_account_ids:
                return {str(random.choice(po_account_ids)): 100.0}
            if move_type == "out_invoice" and so_account_ids:
                return {str(random.choice(so_account_ids)): 100.0}
            return None

        def create_bills(file_name):
            with open(data_path / file_name, mode="r", encoding="utf-8-sig") as csv_file:
                for n, row in enumerate(csv.DictReader(csv_file)):
                    move_type = row.get("move_type")
                    dict_line = {
                        "product_id": products.get((row.get("invoice_line_ids/product_name") or "").lower()),
                        "quantity": row.get("invoice_line_ids/quantity"),
                        "price_unit": float((row.get("invoice_line_ids/price_unit") or "0").replace(",", "")),
                    }
                    if demo.accountant:
                        analytic = _analytic_distribution(move_type)
                        if analytic:
                            dict_line["analytic_distribution"] = analytic

                    move_id = Move.create({
                        "partner_id": partners.get((row.get("partner_name") or "").lower()),
                        "invoice_date": row.get("date"),
                        "amount_untaxed": float((row.get("amount_untaxed") or "0").replace(",", "")),
                        "invoice_date_due": row.get("invoice_date_due"),
                        "move_type": move_type,
                        "date": row.get("date"),
                    })
                    dict_line["move_id"] = move_id.id
                    try:
                        MoveLine.create(dict_line)
                        move_id.action_post()
                        if n > 5 and move_id.move_type == "out_invoice":
                            self.env["account.move.send"]._generate_and_send_invoices(move_id)

                    except Exception as e:
                        _logger.error("Invoice error:", e)

        def create_transaction(is_in_invoice: bool, limit=8, threshold=6):
            BankLine = self.env["account.bank.statement.line"].with_company(company)
            bills = Move.search([("move_type", "=", "in_invoice" if is_in_invoice else "out_invoice"),
                                 ("company_id", "=", company_id), ], limit=limit, order="invoice_date_due asc", )

            for n, bill in enumerate(bills):
                new_transaction = {
                    "date": bill.invoice_date,
                    "move_type": bill.move_type,
                    "amount": bill.amount_total * (-1 if is_in_invoice else 1),
                }
                if n < threshold:
                    new_transaction["payment_ref"] = bill.name
                    new_transaction["partner_id"] = bill.partner_id.id
                else:
                    new_transaction["payment_ref"] = bill.partner_id.name
                BankLine.create(new_transaction)

        create_bills("customer_bills.csv")
        create_bills("supplier_bills.csv")
        create_transaction(False, limit=16, threshold=14)
        create_transaction(True)

        FollowupLine = self.env["account_followup.followup.line"].with_company(company)
        with open(data_path / "followups.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for n, row in enumerate(csv.DictReader(csv_file)):
                vals = {
                    "name": row.get("name"),
                    "delay": row.get("delay"),
                    "create_activity": row.get("create_activity"),
                    "mail_template_id": self.env["mail.template"].search(
                        [("name", "ilike", row.get("mail_template_id"))], limit=1).id,
                    "auto_execute": row.get("auto_execute") if n >= 2 else False,
                }

                if vals.get("create_activity"):
                    vals.update({
                        "activity_type_id": self.env["mail.activity.type"].search(
                            [("name", "ilike", row.get("activity_type_id"))], limit=1).id,
                        "activity_summary": row.get("activity_summary"),
                        "activity_note": row.get("activity_note"),
                    })
                FollowupLine.create(vals)

        Partner.search([("followup_status", "=", "in_need_of_action")]).action_manually_process_automatic_followups()

    def _load_data_asset(self):
        demo = self.demo_data_id
        company = demo.company_id
        Asset = self.env["account.asset"].with_company(company)

        valid_account_ids = {r["id"] for r in
                        self.env["account.account"].search_read([("company_ids", "parent_of", company.id)],["id"])}

        def _account_id(raw):
            try:
                aid = int(raw or 0)
            except (ValueError, TypeError):
                return False
            return aid if aid in valid_account_ids else False

        asset_vals = []
        with open(Path(demo.data_path) / "assets.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                asset_vals.append({
                    "method_period": "1",
                    "account_depreciation_id": _account_id(row.get("account_depreciation_id")),
                    "account_depreciation_expense_id": _account_id(row.get("account_depreciation_expense_id")),
                    "name": row.get("name"),
                    "method": row.get("method"),
                    "original_value": row.get("original_value"),
                    "acquisition_date": row.get("acquisition_date"),
                    "salvage_value": row.get("salvage_value"),
                    "method_number": row.get("method_number"),
                })
        Asset.create(asset_vals).validate()

    def _load_data_pdf(self):
        demo = self.demo_data_id
        data_path = Path(demo.data_path)
        Move = self.env["account.move"]
        pdf_domain = [("move_type", "=", "in_invoice"),("invoice_pdf_report_file", "=", False),
            ("company_id", "=", demo.company_id.id),]
        pdfkit_options = {"encoding": "UTF-8"}

        for invoice in Move.search(pdf_domain, limit=5):
            pdf_path = data_path / f"invoice{invoice.id}.pdf"

            if pdf_path.exists():
                invoice.invoice_pdf_report_file = base64.b64encode(pdf_path.read_bytes())
                continue

            line = invoice.line_ids[0]
            new_prompt = self.prompt
            for key, value in {
                "__supplier_name__": invoice.partner_id.name,
                "__supplier_city__": invoice.partner_id.city,
                "__supplier_country__": invoice.partner_id.country_id.name,
                "__name__": demo.name,
                "__city__": demo.city,
                "__country__": demo.country_id.name,
                "__product_name__": line.product_id.name,
                "__product_qty__": str(line.quantity),
                "__product_price__": str(line.price_unit),
                "__bill_date__": str(invoice.invoice_date),
                "__due_date__": str(invoice.invoice_date_due),
                "__tax_total__": str(invoice.amount_total),
            }.items():
                new_prompt = new_prompt.replace(key, value or "<UNKNOWN>")

            result = cap_api_call.mistral_api_call(
                MISTRAL_SMALL, new_prompt, demo.api_key_id.name, demo.lang
            )
            html = result.choices[0].message.content
            html_match = _HTML_BLOCK_RE.search(html)

            pdf_bytes = pdfkit.from_string(html_match.group(1), False, options=pdfkit_options)
            pdf_path.write_bytes(pdf_bytes)
            invoice.invoice_pdf_report_file = base64.b64encode(pdf_bytes)

        if Move.search(pdf_domain, limit=1):
            self.copy({"state": "in_line", "nb_tries": 0})

    def _load_data_crm(self):
        demo = self.demo_data_id
        data_path = Path(demo.data_path)
        company = demo.company_id
        company_id = company.id

        existing_tag_names = {r["name"].lower() for r in self.env["crm.tag"].search_read([], ["name"])}
        tag_vals = []
        with open(data_path / "crm_tags.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                name = row.get("name") or ""
                if name.lower() not in existing_tag_names:
                    tag_vals.append({"name": name, "color": row.get("color")})
                    existing_tag_names.add(name.lower())
        if tag_vals:
            self.env["crm.tag"].create(tag_vals)

        Lead = self.env["crm.lead"].with_company(company)
        Lead.search([("company_id", "=", company_id)]).unlink()

        employees = {emp.name: emp for emp in self.env["hr.employee"].search([("company_id", "=", company_id)])}
        users = {u.name: u for u in self.env["res.users"].search([])}
        user_ids_with_employee = {r["user_id"][0] for r in self.env["hr.employee"].search_read(
            [("company_id", "=", company_id), ("user_id", "!=", False)], ["user_id"])}

        stages = {r["name"]: r["id"] for r in self.env["crm.stage"].search_read([], ["name", "id"])}
        partners = {r["name"].lower(): r["id"] for r in
                    self.env["res.partner"].with_company(company).search_read([], ["name", "id"])}
        crm_tags = {r["name"].lower(): r["id"] for r in self.env["crm.tag"].search_read([], ["name", "id"])}
        all_company_ids = self.env["res.company"].search([]).ids

        with open(data_path / "crm_leads.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for n, row in enumerate(csv.DictReader(csv_file)):
                user_name = row.get("user_id", "").strip()
                employee = employees.get(user_name)
                if not employee:
                    continue

                user_exist = users.get(user_name)
                user_partner_ids = employee._get_related_partners()
                user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                if not user_exist:
                    new_user = self.env["res.users"].create({
                        "company_id": company_id,
                        "name": user_name,
                        "login": user_name,
                        "work_phone": employee.work_phone,
                        "work_email": employee.work_email,
                        "company_ids": [(6, 0, all_company_ids)],
                        "employee_ids": [(4, employee.id)],
                        "partner_id": user_partner_id,
                    })
                    users[user_name] = new_user
                    user_exist = new_user
                else:
                    if not employee.user_id and user_exist.id not in user_ids_with_employee:
                        employee.user_id = user_exist.id
                        user_ids_with_employee.add(user_exist.id)
                    user_exist.write({
                        "company_id": company_id,
                        "name": user_name,
                        "login": user_name if user_exist.id != 2 else user_exist.login,
                        "company_ids": [(6, 0, all_company_ids)],
                        "work_phone": employee.work_phone,
                        "work_email": employee.work_email,
                    })

                tag_name = (row.get("tag_ids") or "").lower()
                tag_id = crm_tags.get(tag_name)

                lead = Lead.create({
                    "stage_id": stages.get(row.get("stage_name")),
                    "partner_id": partners.get((row.get("partner_id") or "").lower()),
                    "user_id": user_exist.id,
                    "name": row.get("name"),
                    "tag_ids": [(6, 0, [tag_id])] if tag_id else [],
                    "expected_revenue": row.get("expected_revenue"),
                    "probability": row.get("probability"),
                    "email_from": row.get("email_from"),
                    "date_deadline": row.get("date_deadline"),
                    "priority": row.get("priority"),
                })

                if 3 <= n < 5:
                    lead.action_set_won_rainbowman()

        self.env["res.users"].search([]).write({"company_ids": [(6, 0, all_company_ids)]})

        crm_model_id = self.env["ir.model"]._get("crm.lead").id
        activity_types = {r["name"]: r["id"] for r in self.env["mail.activity.type"].search_read([], ["name", "id"])}
        leads_by_name = {r["name"].lower(): (r["id"], r["user_id"][0] if r["user_id"] else False)
            for r in Lead.search_read([("company_id", "=", company_id)], ["name", "id", "user_id"])}
        Activity = self.env["mail.activity"].with_company(company)

        with open(data_path / "crm_activities.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                lead_name = (row.get("lead_name") or "").lower()
                lead_id, lead_user_id = leads_by_name.get(lead_name, (False, False))
                Activity.create({
                    **{k: v for k, v in row.items() if k not in {"activity_type_id", "lead_name"}},
                    "activity_type_id": activity_types.get(row.get("activity_type_id")),
                    "res_model": "crm.lead",
                    "res_model_id": crm_model_id,
                    "res_id": lead_id,
                    "user_id": lead_user_id,
                })

    def _load_data_sales(self):
        demo = self.demo_data_id
        company = demo.company_id
        company_id = company.id
        data_path = Path(demo.data_path)
        settings = self.env["res.config.settings"].create({"group_sale_order_template": True,
                                                           "group_discount_per_so_line": True,
                                                           "module_sale_margin": True,})
        settings.execute()

        self.env["product.template"].create({
            "name": "Delivery",
            "default_code": "DEL_001",
            "list_price": 1.0,
            "standard_price": 1.0,
            "type": "service",
            "company_id": company_id,
        })

        self.env["product.template"].create({
            "name": "Web Implementation",
            "default_code": "SERVICE_001",
            "list_price": 1.0,
            "standard_price": 1.0,
            "type": "service",
            "company_id": company_id,
        })

        pricelist_id = self.env["product.pricelist"].search(
            [("name", "ilike", f"Default {demo.name}"), ("company_id", "=", company_id)], limit=1)
        if not pricelist_id:
            pricelist_id = self.env["product.pricelist"].create({
                "name": f"Default {demo.name}",
                "company_id": company_id,
                "currency_id": company.currency_id.id,
            })

        is_stock: bool = bool(
            self.env["ir.module.module"].search([("name", "=", "stock")], limit=1).state
            in ["installed", "to_remove", "to_upgrade"] or demo.stock)

        if is_stock:
            warehouse = self.env["stock.warehouse"].search([("name", "=", demo.name)], limit=1)
            if not warehouse:
                self.env["stock.warehouse"].with_company(company).create({"name": demo.name, "code": "STOCK"})

                self.env["ir.config_parameter"].with_company(company).sudo().set_param("stock.sms_confirmation",
                                                                                       "False")

        partner_map = {r["name"].lower(): r["id"] for r in self.env["res.partner"].with_company(company).search_read(
                [("company_id", "in", [False, company_id])], ["name", "id"])}

        product_map = {r["name"]: {"id": r["id"], "type": r["type"]}
            for r in self.env["product.product"].with_company(company).search_read(
                [("company_id", "in", [False, company_id])], ["name", "id", "type"])}

        SaleOrder = self.env["sale.order"].with_company(company)
        SaleOrderLine = self.env["sale.order.line"].with_company(company)
        Picking = self.env["stock.picking"].with_company(company) if is_stock else None

        analytic_accounts = (self.env["account.analytic.account"].with_company(company).search(
                [("code", "ilike", "SO")]) if demo.accountant else None)

        with open(data_path / "sales.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for n, row in enumerate(csv.DictReader(csv_file)):
                amount_untaxed = float((row.get("amount_untaxed") or "0").replace(",", ""))
                product_name = row.get("order_line/product_id")
                quantity = row.get("order_line/quantity")
                price_unit = float((row.get("price_unit") or "0").replace(",", ""))

                partner_key = (row.get("partner_id") or "").lower()
                partner_id = next((v for k, v in partner_map.items() if partner_key in k),False,)
                product_info = product_map.get(product_name, {})
                product_id = product_info.get("id")

                dict_line = {
                    "name": product_name,
                    "product_id": product_id,
                    "product_uom_qty": quantity,
                    "price_unit": price_unit,
                }

                order_id = SaleOrder.create({
                    "name": row.get("name"),
                    "partner_id": partner_id,
                    "date_order": row.get("date_order"),
                    "amount_untaxed": amount_untaxed,
                    "pricelist_id": pricelist_id.id,
                    "company_id": company_id,
                })

                if 3 <= n < 5:
                    order_id.action_quotation_send()
                elif n >= 5:
                    if analytic_accounts:
                        random_account = random.choice(analytic_accounts)
                        dict_line["analytic_distribution"] = {str(random_account.id): 100.0}
                    order_id.action_confirm()

                dict_line["order_id"] = order_id.id
                SaleOrderLine.create(dict_line)

                if n >= 10 and is_stock and product_info.get("type") == "consu":
                    try:
                        picking = Picking.search([("origin", "=", order_id.name)], limit=1)
                        picking.button_validate()
                        if picking.state != "done":
                            for move in picking.move_ids:
                                move._action_done()
                    except Exception as e:
                        _logger.error("Delivery error:\n", e)
                if n >= 15:
                    has_non_service = any(line.product_id and line.product_id.type != "service"
                        for line in order_id.order_line)

                    if has_non_service:
                        if not is_stock:
                            order_id.order_line[0].qty_delivered = order_id.order_line[0].product_uom_qty
                        try:
                            invoices = order_id._create_invoices()
                            if is_stock and dict_line.get("analytic_distribution"):
                                invoices.invoice_line_ids.analytic_distribution = dict_line["analytic_distribution"]
                            invoices.action_post()
                        except Exception as e:
                            _logger.error("Invoice Exception:\n", e)

        Template = self.env["sale.order.template"].with_company(company)
        Template.search([("company_id", "=", False)]).unlink()

        customer_partners = self.env["res.partner"].with_company(company).search(
            [("category_id.name", "ilike", "customer")])

        is_spreadsheet = bool(
            self.env["ir.module.module"].search([("name", "=", "spreadsheet_sale_management")], limit=1
            ).state in ["installed", "to_remove", "to_upgrade"])

        if is_spreadsheet:
            self.env.cr.execute("""
                DELETE FROM spreadsheet_revision
                WHERE res_model = 'sale.order.spreadsheet'
                AND res_id NOT IN (SELECT id FROM sale_order_spreadsheet)
            """)

        confirm_mail_template_id = self.env["mail.template"].search([("name", "ilike", "Sales: Order Confirmation")],
                                                                    limit=1).id
        Spreadsheet = self.env["sale.order.spreadsheet"].with_company(company) if is_spreadsheet else None

        with open(data_path / "quotation_templates.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                products = row.get("order_line/product_id").split(";")
                quantities = row.get("order_line/quantity").split(";")
                lines = []
                for p, quantity in zip(products, quantities):
                    p = p.strip()
                    p_lower = p.lower()
                    prod_id = next((info["id"] for name, info in product_map.items() if p_lower in name.lower()), None)
                    lines.append((0, 0, {
                        "product_id": prod_id,
                        "product_uom_qty": quantity,
                        "name": p,
                    }))

                product_ids = [vals.get("product_id") for cmd, _id, vals in lines if vals.get("product_id")]
                products = self.env["product.product"].browse(product_ids)

                if any(p.type == "consu" for p in products):
                    lines.append((0, 0, {
                        "product_id": self.env["product.product"].search([("default_code","=", "DEL_001"),
                                                                          ("company_id","=",company_id)],limit=1).id,
                        "product_uom_qty": 1,
                        "name": "Delivery",
                    }))

                else:
                    lines.append((0, 0, {
                        "product_id": self.env["product.product"].search([("default_code", "=", "SERVICE_001"),
                                                                          ("company_id", "=", company_id)], limit=1).id,
                        "product_uom_qty": 1,
                        "name": "Service",
                    }))

                lines.append((0, 0, {
                    "name": row.get("note"),
                    "display_type": "line_note",
                }))

                created_template = Template.create({
                    "name": row.get("name"),
                    "mail_template_id": confirm_mail_template_id,
                    "sale_order_template_line_ids": lines,
                    "note": row.get("terms_and_conditions"),
                })

                if is_spreadsheet:
                    with self.env.cr.savepoint():
                        module_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

                        if any(p.type == "consu" for p in products):
                            file_path = os.path.join(module_path, 'data', 'spreadsheets', 'Stored_Bundle.osheet.json')

                        else:
                            file_path = os.path.join(module_path, 'data', 'spreadsheets', 'Service_Bundle.osheet.json')


                        spreadsheet_data = False
                        if os.path.exists(file_path):
                            with open(file_path, 'rb') as f:
                                spreadsheet_data = base64.b64encode(f.read())

                        spreadsheet = Spreadsheet.create({
                            "name": row.get("name"),
                            "spreadsheet_binary_data": spreadsheet_data,
                            "company_id": company_id,
                        })
                        created_template.spreadsheet_template_id = spreadsheet.id

                sale_order_id = SaleOrder.create({
                    "partner_id": random.choice(customer_partners).id if customer_partners else False,
                    "sale_order_template_id": created_template.id,
                    "company_id": company_id,
                    "pricelist_id": pricelist_id.id,
                })
                sale_order_template = created_template.with_context(lang=sale_order_id.partner_id.lang)

                order_lines_data = [fields.Command.clear()]
                order_lines_data += [fields.Command.create(line._prepare_order_line_values())
                    for line in sale_order_template.sale_order_template_line_ids]

                if len(order_lines_data) >= 2:
                    order_lines_data[1][2]["sequence"] = -99

                sale_order_id.write({"order_line": order_lines_data})



    def _load_header_pdf(self):
        demo = self.demo_data_id
        company = demo.company_id
        company_id = company.id
        pdfkit_options = {"encoding": "UTF-8"}

        QuotDoc = self.env["quotation.document"].with_company(company)
        QuotDoc.search([("document_type", "=", "header")]).unlink()

        quot_template_ids = (
            self.env["sale.order.template"].with_company(company).search([("company_id", "=", company_id)]).ids)

        for i in range(1, 4):
            new_prompt = self.prompt
            for key, value in {
                "__company_name__": demo.name,
                "__city__": demo.city,
                "__country__": demo.country_id.name,
                "__index__": str(i),}.items():
                new_prompt = new_prompt.replace(key, value or "<UNKNOWN>")

            result = cap_api_call.mistral_api_call(
                MISTRAL_SMALL,
                new_prompt,
                demo.api_key_id.name,
                demo.lang,
            )

            html = result.choices[0].message.content
            match = _HTML_BLOCK_RE.search(html)
            pdf_data = base64.b64encode(
                pdfkit.from_string(match.group(1) if match else html, False, options=pdfkit_options))

            QuotDoc.create({
                "name": f"Quotation Header {i}",
                "document_type": "header",
                "datas": pdf_data,
                "mimetype": "application/pdf",
                "quotation_template_ids": [(6, 0, quot_template_ids)],
            })

    def _load_footer_pdf(self):
        demo = self.demo_data_id
        company = demo.company_id
        company_id = company.id
        pdfkit_options = {"encoding": "UTF-8"}

        QuotDoc = self.env["quotation.document"].with_company(company)
        QuotDoc.search([("document_type", "=", "footer")]).unlink()

        quot_template_ids = (
            self.env["sale.order.template"].with_company(company).search([("company_id", "=", company_id)]).ids)

        for i in range(1, 4):
            new_prompt = self.prompt
            for key, value in {
                "__company_name__": demo.name,
                "__city__": demo.city,
                "__country__": demo.country_id.name,
                "__index__": str(i),}.items():
                new_prompt = new_prompt.replace(key, value or "<UNKNOWN>")

            result = cap_api_call.mistral_api_call(
                MISTRAL_SMALL,
                new_prompt,
                demo.api_key_id.name,
                demo.lang,
            )

            html = result.choices[0].message.content
            match = _HTML_BLOCK_RE.search(html)
            pdf_data = base64.b64encode(
                pdfkit.from_string(match.group(1) if match else html, False, options=pdfkit_options)
            )

            QuotDoc.create({
                "name": f"Quotation Footer {i}",
                "document_type": "footer",
                "datas": pdf_data,
                "mimetype": "application/pdf",
                "quotation_template_ids": [(6, 0, quot_template_ids)],
            })

        sale_orders = self.env["sale.order"].with_company(company).search(
            [("sale_order_template_id", "!=", False), ("company_id", "=", company_id)])

        headers = QuotDoc.search([("document_type", "=", "header"), ("company_id", "=", company_id)])
        footers = QuotDoc.search([("document_type", "=", "footer"), ("company_id", "=", company_id)])

        template = self.env.ref("sale.email_template_edi_sale", raise_if_not_found=False)
        for n, order in enumerate(sale_orders):
            random_header = random.choice(headers) if headers else False
            random_footer = random.choice(footers) if footers else False

            order.quotation_document_ids = [(6, 0, (random_header | random_footer).ids)]
            if n < 2:
                wizard = order.env["mail.compose.message"].with_context(
                    validate_analytic=True,
                    check_document_layout=True,
                    active_model="sale.order",
                    active_id=order.id,
                    default_model="sale.order",
                    default_composition_mode="comment",
                    default_email_layout_xmlid="mail.mail_notification_layout_with_responsible_signature",
                    email_notification_allow_footer=True,
                    hide_mail_template_management_options=True,
                    proforma=False,
                    force_email=True,
                    default_template_id=template.id,
                    mark_so_as_sent=True,
                ).create({
                    "message_type": "comment",
                    "composition_mode": "comment",
                    "model": "sale.order",
                    "res_ids": str([order.id]),
                    "template_id": template.id,
                })

                wizard._action_send_mail(auto_commit=True)


    def _load_data_purchase(self):
        demo = self.demo_data_id
        company = demo.company_id
        company_id = company.id
        data_path = Path(demo.data_path)

        is_stock: bool = bool(self.env["ir.module.module"].search([("name", "=", "stock")], limit=1).state
            in ["installed", "to_remove", "to_upgrade"] or demo.stock)

        picking_type_id = False
        if is_stock:
            warehouse = self.env["stock.warehouse"].search([("name", "=", demo.name)], limit=1)
            if not warehouse:
                self.env["stock.warehouse"].with_company(company).create({"name": demo.name, "code": "STOCK"})
            PickingType = self.env["stock.picking.type"].with_company(company)
            picking_type = PickingType.search([("name", "=", "Stock"), ("company_id", "=", company_id)], limit=1)
            if not picking_type:
                picking_type = PickingType.create({"name": "Stock", "sequence_code": "STOCK", "company_id": company_id})
            picking_type_id = picking_type.id

        partner_map = {r["name"].lower(): r["id"]
                       for r in self.env["res.partner"].with_company(company).search_read(
                [("company_id", "in", [False, company_id])], ["name", "id"])}
        product_map = {}
        for r in self.env["product.product"].with_company(company).search_read(
            [("company_id", "in", [False, company_id])], ["name", "id", "default_code", "type"]):
            info = {"id": r["id"], "type": r["type"]}
            product_map[r["name"].lower()] = info
            if r["default_code"]:
                product_map[r["default_code"].lower()] = info

        PurchaseOrder = self.env["purchase.order"].with_company(company)
        PurchaseOrderLine = self.env["purchase.order.line"].with_company(company)
        Move = self.env["account.move"].with_company(company)

        analytic_accounts = (self.env["account.analytic.account"].with_company(company).search(
                [("code", "ilike", "PO")]) if demo.accountant else None)

        with open(data_path / "purchase.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for n, row in enumerate(csv.DictReader(csv_file)):
                partner_key = (row.get("partner_id") or "").lower()
                partner_id = next((v for k, v in partner_map.items() if partner_key in k),False,)
                if not partner_id:
                    continue

                product_key = (row.get("order_line/product_id") or "").lower()
                product_info = next((info for k, info in product_map.items() if product_key in k),{},)

                dict_line = {
                    "name": row.get("order_line/name"),
                    "product_id": product_info.get("id"),
                    "product_qty": row.get("order_line/quantity"),
                    "price_unit": float((row.get("order_line/price_unit") or "0").replace(",", "")),
                }

                vals = {
                    "partner_id": partner_id,
                    "date_order": row.get("date_order"),
                    "amount_untaxed": float((row.get("amount_untaxed") or "0").replace(",", "")),
                    "date_planned": row.get("date_planned"),
                    "name": row.get("name"),
                }
                if picking_type_id:
                    vals["picking_type_id"] = picking_type_id

                order_id = PurchaseOrder.create(vals)

                if 3 <= n < 5:
                    order_id.action_rfq_send()
                elif n >= 5:
                    if analytic_accounts:
                        random_account = random.choice(analytic_accounts)
                        dict_line["analytic_distribution"] = {str(random_account.id): 100.0}
                    order_id.button_confirm()

                dict_line["order_id"] = order_id.id
                PurchaseOrderLine.create(dict_line)

                if n > 10 and product_info.get("type") == "consu":
                    order_id.action_create_invoice()
                    bill = Move.search([("state", "=", "draft")], limit=1)
                    bill.auto_post = "no"
                    bill.invoice_date = bill.date
                    bill.action_post()

    def _load_data_stock(self):
        demo = self.demo_data_id
        company = demo.company_id
        company_id = company.id
        data_path = Path(demo.data_path)

        Orderpoint = self.env["stock.warehouse.orderpoint"].with_company(company)
        Quant = self.env["stock.quant"].with_company(company)

        Orderpoint.search([("company_id", "=", company_id)]).unlink()
        Quant.search([("company_id", "=", company_id)]).unlink()

        self.env["res.config.settings"].create({
            "group_stock_multi_locations": True,
            "group_stock_adv_location": True,
            "replenish_on_order": True,
        }).execute()

        warehouse = self.env["stock.warehouse"].search([("name", "=", demo.name), ("company_id", "=", company_id)],
                                                       limit=1)
        if not warehouse:
            warehouse = self.env["stock.warehouse"].with_company(company).create({"name": demo.name, "code": "STOCK"})

        self.env["stock.picking.type"].with_company(company).create({"name": "Stock", "sequence_code": "STOCK"})

        location_id = warehouse.lot_stock_id.id
        product_map = {r["name"].lower(): {"id": r["id"], "is_storable": r["type"] == "consu"}
                       for r in self.env["product.product"].with_company(company).search_read(
                [("company_id", "in", [False, company_id])], ["name", "id", "type"])}

        with open(data_path / "stock.csv", mode="r", encoding="utf-8-sig") as csv_file:
            quant_vals = []
            for row in csv.DictReader(csv_file):
                product_key = (row.get("product_id") or "").lower()
                product_info = next((info for k, info in product_map.items() if product_key in k),{},)
                if not product_info.get("is_storable"):
                    continue
                quant_vals.append({
                    "product_id": product_info["id"],
                    "location_id": location_id,
                    "quantity": row.get("inventory_quantity"),
                    "inventory_quantity": row.get("inventory_quantity"),
                    "inventory_date": row.get("inventory_date"),
                })
            Quant.create(quant_vals)

        self.env["ir.config_parameter"].sudo().set_param("stock.sms_confirmation", "False")

        with open(data_path / "replenishments.csv", mode="r", encoding="utf-8-sig") as csv_file:
            for n, reorder_row in enumerate(csv.DictReader(csv_file)):
                product_key = (reorder_row.get("product_id") or "").lower()
                product_info = next((info for k, info in product_map.items() if product_key in k),{},)
                if not product_info or not product_info.get("is_storable"):
                    continue
                rule = Orderpoint.create({
                    "product_id": product_info["id"],
                    "product_min_qty": reorder_row.get("min_qty"),
                    "product_max_qty": reorder_row.get("max_qty"),
                    "qty_to_order": reorder_row.get("qty_to_order"),
                    "trigger": "auto" if n > 5 else "manual",
                    "location_id": location_id,
                })
                if n < 6 and rule.trigger == "manual":
                    rule.write({
                        "product_min_qty": rule.qty_on_hand + 5,
                        "product_max_qty": rule.qty_on_hand + 20,
                    })

        Orderpoint.search([("trigger", "=", "manual")]).action_remove_manual_qty_to_order()

        if (self.env["ir.module.module"].search([("name", "=", "purchase")], limit=1).state in
                ["installed", "to_upgrade", "to_remove"]):
            replenishments = Orderpoint.search([("company_id", "=", company_id)])
            replenishments.product_id.write({
                "seller_ids": [(0, 0, {
                    "partner_id": company.partner_id.id,
                    "min_qty": 1,
                    "price": 0.0,
                })]
            })

    def _load_data_hr(self):
        company = self.demo_data_id.company_id

        with open(os.path.join(self.demo_data_id.data_path, "departments.csv"),
                  mode="r",encoding="utf-8-sig",) as csv_file:
            dept_vals = [{"name": row["name"]} for row in csv.DictReader(csv_file) if row.get("name")]
        if dept_vals:
            self.env["hr.department"].with_company(company).create(dept_vals)

        dept_cache = {d.name: d.id for d in self.env["hr.department"].search([("company_id", "=", company.id)])}

        with open(os.path.join(self.demo_data_id.data_path, "employees.csv"),
                  mode="r",encoding="utf-8-sig",) as csv_file:
            rows = [r for r in csv.DictReader(csv_file) if any(r.values())]

        if not rows:
            return

        self.env.user.name = rows[0]["name"]
        parent_names = []
        employee_vals = []
        for row in rows:
            parent_names.append(row.get("parent_id") or "")
            employee_vals.append({
                "department_id": dept_cache.get(row.get("department_id")),
                "name": row.get("name"),
                "job_title": row.get("job_title"),
                "work_email": row.get("work_email"),
                "work_phone": row.get("work_phone"),
                "hourly_cost": row.get("hourly_cost"),
                "sex": row.get("sex"),
            })
        employees = self.env["hr.employee"].with_company(company).create(employee_vals)

        emp_cache = {emp.name: emp.id for emp in employees}
        parent_to_emps = {}
        for emp, parent_name in zip(employees, parent_names):
            parent_id = emp_cache.get(parent_name)
            if parent_id:
                parent_to_emps.setdefault(parent_id, self.env["hr.employee"])
                parent_to_emps[parent_id] |= emp
        for parent_id, child_emps in parent_to_emps.items():
            child_emps.write({"parent_id": parent_id})

        current_user = self.env.user
        matched = employees.filtered(lambda e: current_user.name.lower() in e.name.lower())
        if matched:
            matched[0].user_id = current_user.id

    def _load_product_pictures(self):
        image_agent = ImageAgent(self.demo_data_id.api_key_id.name)
        image_agent.get_agent()
        product_images = []
        id_list = []

        products = (self.env["product.template"].with_company(self.demo_data_id.company_id).
                    search([],limit=(self.demo_data_id.percent_product_pictures *
            len(self.env["product.template"].with_company(self.demo_data_id.company_id).search([]))),))

        for product in products:
            path = os.path.join(self.demo_data_id.data_path,
                (product.name.lower().replace(" ", "_") + ".jpg").strip(),)

            if os.path.isfile(path):
                with open(path, "rb") as image:
                    product.image_1920 = base64.b64encode(image.read())
                product_images.append(path)
                id_list.append(product.id)
                continue

            new_prompt = (self.prompt.replace("__name__", self.demo_data_id.name).replace("__product__", product.name))

            dir_name = os.path.dirname(path)
            if dir_name and not os.path.exists(dir_name):
                continue

            image_agent.save_image(new_prompt, path)

            if not os.path.isfile(path):
                continue

            with open(path, "rb") as image:
                product.image_1920 = base64.b64encode(image.read())

            product_images.append(path)
            id_list.append(product.id)


    def _load_pictures_hr(self):
        def crop_and_save(path: str, employee) -> str:
            im = Image.open(path)
            width, height = im.size
            im = im.crop((width / 4, 0, (width / 4) * 3, height))
            cropped = os.path.join(self.demo_data_id.data_path,
                ("cropped_" + employee.name.lower().replace(" ", "_") + ".jpg").strip(),)
            im.save(cropped)
            return cropped

        image_agent = ImageAgent(self.demo_data_id.api_key_id.name)
        image_agent.get_agent()
        male_employee_picture = []
        female_employee_picture = []
        id_list = []
        for employee in self.env["hr.employee"].search([("company_id", "=", self.demo_data_id.company_id.id)],
            limit=(self.demo_data_id.percent_employees_pictures * self.demo_data_id.nb_employees),):
            path = os.path.join(self.demo_data_id.data_path,
                (employee.sex[0] + "_" + employee.name.lower().replace(" ", "_") + ".jpg").strip(),)
            if employee.sex == "male":
                male_employee_picture.append(path)
            if employee.sex == "female":
                female_employee_picture.append(path)
            id_list.append(employee.id)
            if os.path.isfile(path):
                cropped = crop_and_save(path, employee)
                with open(cropped, "rb") as image:
                    employee.image_1920 = base64.b64encode(image.read())
                continue
            new_prompt = self.prompt
            new_prompt = (
                new_prompt.replace("__name__", self.demo_data_id.name)
                .replace("__employee__", employee.name)
                .replace("__job__", employee.job_title)
                .replace("__department__", employee.department_id.name)
            )
            image_agent.save_image(new_prompt, path)
            cropped = crop_and_save(path, employee)
            with open(cropped, "rb") as image:
                employee.image_1920 = base64.b64encode(image.read())
        nb_m = 0
        nb_f = 0

        def assign_picture(iterator: int, path_list: list[str], employee) -> int:
            if not path_list:
                return 0
            cropped = crop_and_save(path_list[iterator % len(path_list)], employee)
            with open(cropped, "rb") as image:
                employee.image_1920 = base64.b64encode(image.read())
            return 1

        for employee in self.env["hr.employee"].search([("id", "not in", id_list)]):
            if employee.sex == "male":
                nb_m += assign_picture(nb_m, male_employee_picture, employee)
            if employee.sex == "female":
                nb_f += assign_picture(nb_f, female_employee_picture, employee)

    def _load_mrp_bom(self):
        company = self.demo_data_id.company_id
        company_restricted = self.demo_data_id.bool_company_restricted

        with open(os.path.join(self.demo_data_id.data_path, "component_products.csv"),
            "r",encoding="utf-8-sig",) as csv_file:
            comp_rows = list(csv.DictReader(csv_file))
        route_names = {row.get("route_ids") for row in comp_rows if row.get("route_ids")}
        route_cache = {r.name: r.ids
            for r in self.env["stock.route"].search([("name", "in", list(route_names)),
                                                     ("company_id", "=", company.id)])}

        product_vals = []
        for row in comp_rows:
            product_vals.append({
                "name": row.get("name"),
                "list_price": row.get("list_price"),
                "default_code": row.get("default_code"),
                "purchase_ok": row.get("purchase_ok"),
                "route_ids": [(6, 0, route_cache.get(row.get("route_ids"), []))],
                "company_id": company.id if company_restricted else False,
            })

        if product_vals:
            self.env["product.template"].with_company(company).create(product_vals)


        with open(os.path.join(self.demo_data_id.data_path, "bom.csv"),"r",encoding="utf-8-sig",) as csv_file:
            bom_rows = list(csv.DictReader(csv_file))

        product_cache = {p.name.lower(): p.id for p in self.env["product.template"].with_company(company).search([])}

        bom_vals = []
        for row in bom_rows:
            product_id = product_cache.get((row.get("product_tmpl_id") or "").lower())
            if not product_id:
                continue
            bom_vals.append({
                "product_tmpl_id": product_id,
                "product_qty": row.get("product_qty"),
                "code": row.get("code"),
            })
        if bom_vals:
            self.env["mrp.bom"].with_company(company).create(bom_vals)

        settings = self.env['res.config.settings'].create({
            'group_uom': True,
            'group_mrp_routings': True,
        })
        settings.execute()

    def _load_mrp_components(self):
        company = self.demo_data_id.company_id

        with open(os.path.join(self.demo_data_id.data_path, "bom_components.csv"),
            "r",encoding="utf-8-sig",) as csv_file:
            rows = list(csv.DictReader(csv_file))

        if not rows:
            return

        bom_codes = {row.get("code") for row in rows if row.get("code")}
        bom_cache = {b.code: (b.id, b.product_tmpl_id.id)
            for b in self.env["mrp.bom"].search([("code", "in", list(bom_codes)), ("company_id", "=", company.id)])}

        product_cache = {p.name.lower(): p.id for p in self.env["product.product"].with_company(company).search([])}

        line_vals = []
        for row in rows:
            bom_entry = bom_cache.get(row.get("code"))
            if not bom_entry:
                continue
            bom_id, bom_product_tmpl_id = bom_entry

            product_id = product_cache.get((row.get("product_id") or "").lower())
            if not product_id or product_id == bom_product_tmpl_id:
                continue

            line_vals.append({
                "bom_id": bom_id,
                "product_id": product_id,
                "product_qty": row.get("product_qty"),
            })

        if line_vals:
            self.env["mrp.bom.line"].with_company(company).create(line_vals)


    def _load_work_centers(self):
        self.env["ir.config_parameter"].set_param("mrp.group_mrp_routings", True)
        self.env.user.group_ids += self.env.ref("mrp.group_mrp_routings")
        with open(os.path.join(self.demo_data_id.data_path, "work_centers.csv"), "r",
                  encoding="utf-8-sig", ) as csv_file:
            vals_list = [{
                    "name": row.get("name"),
                    "code": row.get("code"),
                    "time_efficiency": float(row.get("time_efficiency", 0)) * 100,
                    "costs_hour": row.get("costs_hour"),
                }for row in csv.DictReader(csv_file)]
        if vals_list:
            self.env["mrp.workcenter"].with_company(self.demo_data_id.company_id).create(vals_list)

    def _load_bom_operations(self):
        with open(os.path.join(self.demo_data_id.data_path, "work_orders.csv"),
            "r",encoding="utf-8-sig",) as csv_file:
            rows = list(csv.DictReader(csv_file))

        if not rows:
            return

        company_id = self.demo_data_id.company_id.id

        wc_codes = {row.get("workcenter_id") for row in rows if row.get("workcenter_id")}
        wc_cache = {wc.code: wc.id
            for wc in self.env["mrp.workcenter"].search(
                [("code", "in", list(wc_codes)), ("company_id", "=", company_id)])}

        bom_codes = {row.get("bom_id") for row in rows if row.get("bom_id")}
        bom_cache = {b.code: b.id
            for b in self.env["mrp.bom"].search([("code", "in", list(bom_codes)), ("company_id", "=", company_id)])}

        vals_list = [{
            "workcenter_id": wc_cache.get(row.get("workcenter_id")),
            "bom_id": bom_cache.get(row.get("bom_id")),
            "name": row.get("name"),
            "employee_ratio": row.get("employee_ratio"),
            "time_cycle_manual": row.get("time_cycle_manual"),
        } for row in rows]
        if vals_list:
            self.env["mrp.routing.workcenter"].with_company(self.demo_data_id.company_id).create(vals_list)

    def _load_manufacturing_orders(self):
        with open(os.path.join(self.demo_data_id.data_path, "manufacturing_orders.csv"),
            "r",encoding="utf-8-sig",) as csv_file:
            rows = list(csv.DictReader(csv_file))

        product_cache = {p.name.lower(): p.id
            for p in self.env["product.product"].with_company(self.demo_data_id.company_id).search([])}

        states = []
        mo_vals = []
        for row in rows:
            states.append(row.get("state"))
            mo_vals.append({
                "product_id": product_cache.get((row.get("product_id") or "").lower()),
                "product_qty": row.get("product_qty"),
                "date_start": row.get("date_start"),
            })

        mos = self.env["mrp.production"].with_company(self.demo_data_id.company_id).create(mo_vals)
        for mo_id in mos:
            mo_id.action_confirm()

        in_progress = self.env["mrp.production"].browse([mo.id for mo, s in zip(mos, states) if s == "in_progress"])
        if in_progress:
            for mo in in_progress:
                mo.action_start()

        for mo, state in zip(mos, states):
            if state == "done":
                try:
                    mo.button_mark_done()
                except Exception:
                    pass

        current_user_emp = self.env["hr.employee"].search(
            [("user_id", "=", self.env.user.id),("company_id", "=", self.demo_data_id.company_id.id)], limit=1)
        if not current_user_emp:
            self.env['hr.employee'].with_company(self.demo_data_id.company_id).create({
                'name': self.env.user.name,
                'user_id': self.env.user.id,
            })

        self._create_unbuild_scrap_ecos()

    def _load_bom_instructions(self):
        file_path = os.path.join(self.demo_data_id.data_path, "operation_instructions.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)

            picking_type = self.env["stock.picking.type"].search(
                [("code", "=", "mrp_operation"),("company_id", "=", self.demo_data_id.company_id.id)], limit=1)
            test_type = self.env["quality.point.test_type"].search([("technical_name", "=", "instructions")], limit=1)

            product_name_change = False
            for row in csv_reader:
                product = self.env["product.template"].with_company(self.demo_data_id.company_id).search(
                    [("name", "=", row.get("product_ids", "").strip())], limit=1)

                product_name_change = product
                if not product:
                    continue

                bom = self.env["mrp.bom"].search([("product_tmpl_id", "=", product.id),
                                                  ("company_id", "=", self.demo_data_id.company_id.id)], limit=1)
                if not bom:
                    continue

                operation = self.env["mrp.routing.workcenter"].search(
                    [("bom_id", "=", bom.id),("name", "=", row.get("name", "").strip()),
                     ("company_id", "=", self.demo_data_id.company_id.id)],limit=1,)
                if not operation:
                    continue

                row_copy = {
                    "name": row.get("name"),
                    "product_ids": [(6, 0, [product.id])],
                    "operation_id": operation.id,
                    "note": row.get("note"),
                }

                if picking_type:
                    row_copy["picking_type_ids"] = [(6, 0, [picking_type.id])]
                if test_type:
                    row_copy["test_type_id"] = test_type.id

                self.env["quality.point"].with_company(self.demo_data_id.company_id).create(row_copy)

        if product_name_change and '[0USE-THIS-ONE]' not in product_name_change.name:
            product_name_change.write({'name': product_name_change.name + '[0USE-THIS-ONE]'})
            mrp_id = self.env['mrp.production'].search(
                [('product_id', 'in', product_name_change.product_variant_ids.ids),
                 ("company_id", "=", self.demo_data_id.company_id.id)])
            if mrp_id:
                mrp_id.action_confirm()
                mrp_id.button_mark_done()
            else:
                production_id = self.env["mrp.production"].with_company(self.demo_data_id.company_id).create({
                    'product_id': product_name_change.product_variant_ids[:1].id,
                    'product_qty': 10,
                    'date_start': fields.Datetime.now(),
                })
                production_id.action_confirm()
                production_id.button_mark_done()


    def _create_unbuild_scrap_ecos(self):
        company_id = self.demo_data_id.company_id.id
        mrp_production = self.env['mrp.production'].search(
            [('state', '=', 'done'), ("company_id", "=", company_id)], limit=2)
        if mrp_production:
            unbuild_orders = self.env['mrp.unbuild'].create([{
                'product_id': p.product_id.id,
                'product_qty': p.product_qty,
                'mo_id': p.id,
                'company_id': p.company_id.id,
            } for p in mrp_production])
            for order in unbuild_orders:
                order.action_validate()

        mrp_production_scrap = self.env['mrp.production'].search(
            [('state', '=', 'confirmed'), ("company_id", "=", company_id)], limit=2)
        if mrp_production_scrap:
            scrap_orders = self.env['stock.scrap'].create([{
                'product_id': p.product_id.id,
                'scrap_qty': p.product_qty,
                'origin': p.name,
                'company_id': p.company_id.id,
            } for p in mrp_production_scrap])
            for order in scrap_orders:
                order.action_validate()

        product_tmpl = self.env["product.template"].with_company(self.demo_data_id.company_id).create({
            'name': 'Demo Product ECO',
            'type': 'consu',
            'company_id': self.demo_data_id.company_id.id,})

        product_variant = product_tmpl.product_variant_id

        component_1 = self.env["product.product"].with_company(self.demo_data_id.company_id).create({
            'name': 'Component 1',
            'type': 'consu',
            'company_id': self.demo_data_id.company_id.id,})

        workcenter = self.env['mrp.workcenter'].search([("company_id", "=", self.demo_data_id.company_id.id)],limit=1)

        bom = self.env['mrp.bom'].with_company(self.demo_data_id.company_id).create({
            'product_tmpl_id': product_tmpl.id,
            'company_id': self.demo_data_id.company_id.id,
            'bom_line_ids': [(0, 0, {
                'product_id': component_1.id,
                'product_qty': 1.0,
            })],
            'operation_ids': [(0, 0, {
                'name': 'Operation 1',
                'workcenter_id': workcenter.id,
                'company_id': self.demo_data_id.company_id.id,
            })],
        })

        mo_1 = self.env["mrp.production"].with_company(self.demo_data_id.company_id).create({
            'product_id': product_variant.id,
            'bom_id': bom.id,
            'product_qty': 10,
            'date_start': fields.Datetime.now(),
            'company_id': self.demo_data_id.company_id.id,
        })
        mo_1.action_confirm()
        mo_1.button_mark_done()

        eco_type = self.env['mrp.eco.type'].search([], limit=1)
        eco_stage_validated = self.env['mrp.eco.stage'].search([('name', '=', 'Validated')], limit=1)

        if eco_type and eco_stage_validated:
            mrp_eco = self.env['mrp.eco'].with_company(self.demo_data_id.company_id).create({
                'name': product_tmpl.name + ' v2',
                'product_tmpl_id': product_tmpl.id,
                'bom_id': bom.id,
                'company_id': self.demo_data_id.company_id.id,
                'will_update_version': True,
                'type_id': eco_type.id,
                'note': 'This is a demo ECO to show the feature.',
            })

            mrp_eco.action_new_revision()

            component_2 = self.env["product.product"].with_company(self.demo_data_id.company_id).create({
                'name': 'Component 2',
                'type': 'consu',
                'company_id': self.demo_data_id.company_id.id,
            })

            mrp_eco.new_bom_id.write({
                'bom_line_ids': [(0, 0, {
                    'product_id': component_2.id,
                    'product_qty': 1.0,
                })],
                'operation_ids': [(0, 0, {
                    'name': 'Operation 2',
                    'workcenter_id': workcenter.id,
                    'company_id': self.demo_data_id.company_id.id,
                })],
            })

            mrp_eco.write({'stage_id': eco_stage_validated.id})
            mrp_eco.action_apply()

            mo_2 = self.env["mrp.production"].with_company(self.demo_data_id.company_id).create({
                'product_id': product_variant.id,
                'bom_id': mrp_eco.new_bom_id.id,
                'product_qty': 10,
                'date_start': fields.Datetime.now(),
                'company_id': self.demo_data_id.company_id.id,
            })
            mo_2.action_confirm()
            mo_2.button_mark_done()


    def _load_pos_data(self):
        company = self.demo_data_id.company_id
        self.env["product.combo"].search([("company_id", "=", company.id)]).unlink()
        self.env["product.template"].with_company(company).search(
            [("available_in_pos", "=", True), ("company_id", "=", company.id)]).unlink()

        with open(os.path.join(self.demo_data_id.data_path, "pos_products.csv"), "r", encoding="utf-8-sig") as csv_file:
            product_rows = list(csv.DictReader(csv_file))

        PosCategory = self.env["pos.category"].with_company(company)
        cat_cache = {c.name.lower(): c.id for c in PosCategory.search([])}
        missing_cat_names = {row.get("category_id") for row in product_rows
            if row.get("category_id") and row.get("category_id").lower() not in cat_cache}
        if missing_cat_names:
            new_cats = PosCategory.create([{"name": n} for n in missing_cat_names])
            cat_cache.update({c.name.lower(): c.id for c in new_cats})

        product_vals = []
        for row in product_rows:
            cat_name = row.get("category_id")
            cat_id = cat_cache.get(cat_name.lower()) if cat_name else None
            product_vals.append({
                "name": row.get("name"),
                "list_price": row.get("list_price"),
                "default_code": row.get("default_code"),
                "type": "consu",
                "pos_categ_ids": [(6, 0, [cat_id])] if cat_id else [],
                "available_in_pos": True,
                "company_id": company.id,
            })
        if product_vals:
            self.env["product.template"].with_company(company).create(product_vals)

        with open(os.path.join(self.demo_data_id.data_path, "pos_combo_items.csv"), "r",
                  encoding="utf-8-sig") as csv_file:
            combo_rows = list(csv.DictReader(csv_file))

        product_cache = {p.name.lower(): p.id for p in self.env["product.product"].with_company(company).search([])}

        combo_vals = []
        for row in combo_rows:
            item_ids = [
                product_cache[name.strip().lower()]
                for name in (row.get("products") or "").split(";")
                if name.strip() and name.strip().lower() in product_cache
            ]
            combo_vals.append({
                "name": row.get("name"),
                "company_id": company.id,
                "combo_item_ids": [(0, 0, {"product_id": pid}) for pid in item_ids],
            })
        if combo_vals:
            self.env["product.combo"].with_company(company).create(combo_vals)

        combo_ids = self.env["product.combo"].search([("company_id", "=", company.id)], limit=1)
        self.env["product.template"].with_company(company).create({
            "name": "Combo Product 1",
            "type": "combo",
            "available_in_pos": True,
            "combo_ids": [(6, 0, combo_ids.ids)],
            "list_price": sum(combo_ids.combo_item_ids.mapped('product_id.list_price')),
            "company_id": company.id,
        })

    def _load_pos_orders(self):
        company = self.demo_data_id.company_id
        self.env["pos.config"].with_company(company).sudo().load_onboarding_retail_scenario()
        pos_config = self.env["pos.config"].search([("company_id", "=", company.id)], limit=1)

        with open(os.path.join(self.demo_data_id.data_path, "pos_session.csv"), "r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                pos_session = self.env["pos.session"].with_company(company).create({
                    "name": row.get("name"),
                    "start_at": row.get("start_at"),
                    "stop_at": row.get("stop_at"),
                    "config_id": pos_config.id,
                })
                #print('pos_session',pos_session)
                pos_session.action_pos_session_close()

        with open(os.path.join(self.demo_data_id.data_path, "pos_orders.csv"), "r", encoding="utf-8-sig") as csv_file:
            order_rows = list(csv.DictReader(csv_file))

        pos_cash_payment_method_id = self.env["pos.payment.method"].search(
            [("company_id", "=", company.id), ("name", "=", "Cash")], limit=1).id
        if not pos_cash_payment_method_id:
            pos_cash_payment_method_id = self.env["pos.payment.method"].create(
                {"name": "Cash", "company_id": company.id}).id

        session_cache = {s.name.lower(): s.id
            for s in self.env["pos.session"].search([("config_id", "=", pos_config.id)])}
        product_cache = {p.name.lower(): (p.id, p.list_price)
            for p in self.env["product.product"].with_company(company).search([])}
        partner_cache = {p.name.lower(): p.id
            for p in self.env["res.partner"].search([("company_id", "in", [company.id, False])])}

        PosOrder = self.env["pos.order"].with_company(company)
        for order_row in order_rows:
            session_id = session_cache.get((order_row.get("session_id") or "").lower())
            if not session_id:
                continue

            product_info = product_cache.get((order_row.get("product_id") or "").lower())
            if not product_info:
                continue
            product_id, list_price = product_info

            pos_order = PosOrder.create({
                "name": order_row.get("name"),
                "date_order": order_row.get("date_order"),
                "session_id": session_id,
                "lines": [(0, 0, {
                    "product_id": product_id,
                    "qty": 1,
                    "price_unit": list_price,
                    "price_subtotal": list_price,
                    "price_subtotal_incl": list_price,
                })],
                "amount_tax": 0.0,
                "amount_paid": list_price,
                "amount_total": list_price,
                "amount_return": 0.0,
                "partner_id": partner_cache.get((order_row.get("partner_id") or "").lower()),
            })
            pos_order.add_payment({
                'amount': list_price,
                'payment_method_id': pos_cash_payment_method_id,
                'name': 'Cash',
                'pos_order_id': pos_order.id,
            })
            pos_order.action_pos_order_invoice()

        combo_product_info = product_cache.get("combo product 1")
        if combo_product_info:
            combo_product_id, combo_list_price = combo_product_info
            combo_session = self.env["pos.session"].create({
                "name": "Morning Session",
                "start_at": fields.Datetime.now(),
                "stop_at": fields.Datetime.now(),
                "config_id": pos_config.id,
            })
            PosOrder.create({
                "name": "POS-COMBO-1",
                "date_order": fields.Datetime.now(),
                "session_id": combo_session.id,
                "lines": [(0, 0, {
                    "product_id": combo_product_id,
                    "qty": 1,
                    "price_unit": combo_list_price,
                    "price_subtotal": combo_list_price,
                    "price_subtotal_incl": combo_list_price,
                })],
                "amount_tax": 0.0,
                "amount_paid": combo_list_price,
                "amount_total": combo_list_price,
                "amount_return": 0.0,
                "partner_id": next(iter(partner_cache.values()), False),
            })

        all_sessions = self.env["pos.session"].search([("company_id", "=", company.id)])
        for session in all_sessions:
            total_amount = sum(session.order_ids.mapped('amount_total'))
            session.write({'cash_register_balance_end_real': total_amount})

    def _load_field_service_data(self):
        company = self.demo_data_id.company_id
        company_id = company.id
        all_company_ids = self.env["res.company"].search([]).ids
        project_id = self.env["project.project"].with_company(company).search(
            ["|", ("name", "ilike", "Field Service"), ("name", "ilike", "Services sur site"),
             ("company_id", "=", company_id)], limit=1).id

        employee_cache = {e.name: e for e in self.env['hr.employee'].search([('company_id', '=', company_id)])}
        user_cache = {u.name: u for u in self.env['res.users'].search([])}
        user_id_emp_cache = {e.user_id.id: e for e in employee_cache.values() if e.user_id}
        partner_cache = {p.name.lower(): p.id for p in self.env["res.partner"].with_company(company).search([])}
        stage_cache = {s.name: s.id for s in self.env['project.task.type'].search([])}

        worksheet_template = self.env['worksheet.template'].with_company(company).search(
            [("company_id", "=", company_id)], limit=1)
        if not worksheet_template:
            new_template = self.env['worksheet.template'].sudo().create({
                'name': 'Worksheet Template 1',
                'res_model': 'project.task',
                'company_id': company_id,
            })
            new_template._create_demo_data_fsm(model_id=new_template.model_id.id)
            default_form_view = self.env['ir.ui.view'].sudo().search(
                [('model', '=', new_template.model_id.model), ('type', '=', 'form')], limit=1)
            extend_view_id = self.env["ir.ui.view"].sudo().create({
                "type": "form",
                "name": 'template_view_' + new_template.name.replace(' ', '_'),
                "model": new_template.model_id.model,
                "inherit_id": default_form_view.id,
                "arch": """
                            <xpath expr="//form/sheet" position="replace">
                                <sheet>
                                    <group invisible="context.get('studio') or context.get('default_x_project_task_id')">
                                        <div class="oe_title">
                                            <h1>
                                                <field name="x_project_task_id" domain="[('is_fsm', '=', True)]" readonly="1"/>
                                            </h1>
                                        </div>
                                    </group>
                                    <group class="o_fsm_worksheet_form">
                                        <field name="x_service_category"/>
                                        <field name="x_work_description"/>
                                        <field name="x_work_performed"/>
                                        <field name="x_equipment_details"/>
                                        <field name="x_condition_before_service"/>
                                        <field name="x_condition_after_service"/>
                                        <field name="x_date"/>
                                        <field name="x_time_spent"/>
                                        <field name="x_customer_feedback"/>
                                        <field name="x_customer_signature" widget="signature"/>
                                    </group>
                                </sheet>
                            </xpath>
                            """,
            }).id
            new_template._generate_qweb_report_template(form_view_id=extend_view_id)
            worksheet_template = new_template

        with open(os.path.join(self.demo_data_id.data_path, "field_service_tasks.csv"), "r", encoding="utf-8-sig") as csv_file:
            task_rows = list(csv.DictReader(csv_file))

        task_vals_list = []
        for row in task_rows:
            user_name = row.get("user_id", "").strip()
            employee = employee_cache.get(user_name)
            if not employee:
                continue

            user_partner_ids = employee._get_related_partners()
            user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id
            user_exist = user_cache.get(user_name)

            if not user_exist:
                new_user = self.env["res.users"].create({
                    'company_id': company_id,
                    'name': user_name,
                    'login': user_name,
                    'company_ids': [(6, 0, all_company_ids)],
                    'work_phone': employee.work_phone,
                    'work_email': employee.work_email,
                    'employee_ids': [(4, employee.id)],
                    'partner_id': user_partner_id,
                })
                user_cache[user_name] = new_user
                users = new_user
            else:
                existing_emp_same_user = user_id_emp_cache.get(user_exist.id)
                if not employee.user_id and not existing_emp_same_user:
                    employee.user_id = user_exist.id
                user_exist.write({
                    'company_id': company_id,
                    'name': user_name,
                    'login': user_name if user_exist.id != 2 else user_exist.login,
                    'company_ids': [(6, 0, all_company_ids)],
                    'work_phone': employee.work_phone,
                    'work_email': employee.work_email,
                })
                users = user_exist

            task_vals_list.append({
                "name": row.get("name"),
                "description": row.get("description"),
                "project_id": project_id,
                "date_deadline": row.get("date_deadline"),
                "planned_date_begin": row.get("planned_date_begin"),
                "partner_id": partner_cache.get((row.get("partner_id") or "").lower()),
                "user_ids": [(6, 0, [users.id])],
                "company_id": company_id,
                "stage_id": stage_cache.get(row.get("stage_id")),
            })

        self.env['res.users'].search([]).write({'company_ids': [(6, 0, all_company_ids)]})

        if task_vals_list:
            created_tasks = self.env["project.task"].with_company(company).create(task_vals_list)
            created_tasks.write({"worksheet_template_id": worksheet_template.id})

        with open(os.path.join(self.demo_data_id.data_path, "field_service_timesheets.csv"), "r", encoding="utf-8-sig") as csv_file:
            timesheet_rows = list(csv.DictReader(csv_file))

        task_cache = {t.name: t.id for t in self.env["project.task"].search([("company_id", "=", company_id)])}

        timesheet_vals = []
        for row in timesheet_rows:
            emp = employee_cache.get(row.get("employee_id"))
            timesheet_vals.append({
                "name": row.get("name"),
                "task_id": task_cache.get(row.get("task_id")),
                "employee_id": emp.id if emp else False,
                "unit_amount": row.get("unit_amount"),
                "date": row.get("date"),
            })
        if timesheet_vals:
            self.env["account.analytic.line"].with_company(company).create(timesheet_vals)

    def _load_field_service_worksheets(self):
        model_id = self.env["ir.model"].search([("name", "ilike", "Worksheet Template 1")], limit=1)
        if not model_id:
            return

        with open(os.path.join(self.demo_data_id.data_path, "fsm_worksheets.csv"), "r", encoding="utf-8-sig") as csv_file:
            rows = list(csv.DictReader(csv_file))

        if not rows:
            return

        task_cache = {t.name.lower(): t.id
                      for t in self.env["project.task"].search([("company_id", "=", self.demo_data_id.company_id.id)])}

        worksheet_vals = []
        for row in rows:
            task_id = task_cache.get((row.get("task_id") or "").lower())
            worksheet_vals.append({
                "x_service_category": row.get("service_category"),
                "x_work_description": row.get("work_description"),
                "x_equipment_details": row.get("equipment_details"),
                "x_work_performed": row.get("work_performed"),
                "x_condition_before_service": row.get("condition_before_service"),
                "x_condition_after_service": row.get("condition_after_service"),
                "x_date": row.get("date"),
                "x_time_spent": row.get("time_spent"),
                "x_customer_feedback": row.get("customer_feedback"),
                "x_project_task_id": task_id,
            })

        if worksheet_vals:
            self.env[model_id.model].create(worksheet_vals)


    def _load_project_and_tasks(self):
        company = self.demo_data_id.company_id
        company_id = company.id

        self.env["account.analytic.line"].search([("company_id", "=", company_id)]).unlink()
        self.env["project.project"].search(["&", "|",
             ("company_id", "=", company_id),("name", "=", "Field Service"),("partner_id", "!=", False),]).unlink()

        settings = self.env['res.config.settings'].create({'group_project_stages': True})
        settings.execute()

        all_company_ids = self.env["res.company"].search([]).ids
        project_stage_id = self.env['project.project.stage'].search([('name', '=', 'In Progress')], limit=1).id

        employee_cache = {e.name: e for e in self.env['hr.employee'].search([('company_id', '=', company_id)])}
        user_cache = {u.name: u for u in self.env['res.users'].search([])}
        user_id_emp_cache = {e.user_id.id: e for e in employee_cache.values() if e.user_id}
        partner_cache = {p.name.lower(): p.id for p in self.env["res.partner"].with_company(company).search([])}

        def _resolve_user(user_name, employee):
            nonlocal user_id_emp_cache
            user_partner_ids = employee._get_related_partners()
            user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id
            user_exist = user_cache.get(user_name)
            if not user_exist:
                new_user = self.env["res.users"].create({
                    'company_id': company_id,
                    'name': user_name,
                    'login': user_name,
                    'company_ids': [(6, 0, all_company_ids)],
                    'work_phone': employee.work_phone,
                    'work_email': employee.work_email,
                    'employee_ids': [(4, employee.id)],
                    'partner_id': user_partner_id,
                })
                user_cache[user_name] = new_user
                return new_user
            else:
                existing_emp_same_user = user_id_emp_cache.get(user_exist.id)
                if not employee.user_id and not existing_emp_same_user:
                    employee.user_id = user_exist.id
                    user_id_emp_cache[user_exist.id] = employee
                user_exist.write({
                    'company_id': company_id,
                    'name': user_name,
                    'login': user_name if user_exist.id != 2 else user_exist.login,
                    'company_ids': [(6, 0, all_company_ids)],
                    'work_phone': employee.work_phone,
                    'work_email': employee.work_email,
                })
                return user_exist

        with open(os.path.join(self.demo_data_id.data_path, "projects.csv"), "r", encoding="utf-8-sig") as csv_file:
            project_rows = list(csv.DictReader(csv_file))

        project_vals_list = []
        for row in project_rows:
            user_name = row.get("user_id", "").strip()
            employee = employee_cache.get(user_name)
            if not employee:
                continue
            users = _resolve_user(user_name, employee)
            project_vals_list.append({
                "name": row.get("name"),
                "user_id": users.id,
                "privacy_visibility": row.get("privacy_visibility"),
                "stage_id": project_stage_id,
                "company_id": company_id,
                "allow_billable": True,
                "allow_task_dependencies": True,
                "partner_id": partner_cache.get((row.get("partner_id") or "").lower()),
            })

        self.env['res.users'].search([]).write({'company_ids': [(6, 0, all_company_ids)]})

        created_projects = self.env["project.project"].with_company(company).create(project_vals_list)

        stages = ['New', 'In Progress', 'Blocked', 'Ready for Review', 'Done']
        project_ids_cmd = [(4, pid) for pid in created_projects.ids]
        for i, stage in enumerate(stages):
            stage_vals = {
                'fold': stage == 'Done',
                'user_id': False,
                'project_ids': project_ids_cmd,
            }
            stage_exist = self.env['project.task.type'].search([('name', 'ilike', stage)], limit=1)
            if stage_exist:
                stage_exist.write(stage_vals)
            else:
                self.env['project.task.type'].create({**stage_vals, 'name': stage, 'sequence': i + 1})

        project_name_cache = {p.name: p.id for p in created_projects}
        stage_cache = {s.name: s.id for s in self.env['project.task.type'].search([])}

        with open(os.path.join(self.demo_data_id.data_path, "tasks.csv"), "r", encoding="utf-8-sig") as csv_file:
            task_rows = list(csv.DictReader(csv_file))

        project_first_task_cache = {}
        n = 0
        for task_row in task_rows:
            planned_start = task_row.get("planned_date_begin")
            deadline = task_row.get("date_deadline")
            if planned_start and deadline:
                try:
                    if datetime.strptime(planned_start, "%Y-%m-%d") >= datetime.strptime(deadline, "%Y-%m-%d"):
                        continue
                except Exception:
                    continue
            else:
                continue

            user_name = task_row.get("user_ids", "").strip()
            employee = employee_cache.get(user_name)
            if not employee:
                continue

            users = _resolve_user(user_name, employee)
            task_project_id = project_name_cache.get(task_row.get("project_id"))
            depend_task_id = project_first_task_cache.get(task_project_id) if task_project_id else None

            task = self.env["project.task"].with_company(company).create({
                "name": task_row.get("name"),
                "project_id": task_project_id,
                "user_ids": [(4, users.id)] if users else [],
                "description": task_row.get("description"),
                "priority": task_row.get("priority"),
                "date_deadline": deadline,
                "planned_date_begin": planned_start,
                "stage_id": stage_cache.get(task_row.get("stage_id")),
                "depend_on_ids": [(6, 0, [depend_task_id])] if depend_task_id and 5 < n < 20 else [],
            })

            if task_project_id and task_project_id not in project_first_task_cache:
                project_first_task_cache[task_project_id] = task.id
            n += 1

        with open(os.path.join(self.demo_data_id.data_path, "timesheets.csv"), "r", encoding="utf-8-sig") as csv_file:
            timesheet_rows = list(csv.DictReader(csv_file))

        task_name_cache = {t.name: t.id for t in self.env["project.task"].search([("company_id", "=", company_id)])}
        resource_cache = {r.name: r.id for r in self.env['resource.resource'].search([('company_id', '=', company_id)])}

        timesheet_vals = []
        planning_rows = []
        for row in timesheet_rows:
            emp = employee_cache.get(row.get("employee_id"))
            timesheet_vals.append({
                "name": row.get("name"),
                "project_id": project_name_cache.get(row.get("project_id")),
                "task_id": task_name_cache.get(row.get("task_id")),
                "employee_id": emp.id if emp else False,
                "unit_amount": row.get("unit_amount"),
                "date": row.get("date"),
            })
            if emp and resource_cache.get(emp.name):
                planning_rows.append((resource_cache[emp.name], row))

        if timesheet_vals:
            self.env["account.analytic.line"].with_company(company).create(timesheet_vals)

        for resource_id, row in planning_rows:
            duration_hours = float(row.get("unit_amount") or 0.0)
            duration = timedelta(hours=duration_hours)
            start_hour = random.randint(8, 15)
            start_datetime = datetime.combine(Date.from_string(row.get("date")), time(start_hour, 0))
            end_datetime = start_datetime + duration
            if end_datetime.date() != start_datetime.date():
                end_datetime = datetime.combine(start_datetime.date(), time(23, 59))
            created_plan = self.env["planning.slot"].with_company(company).create({
                "resource_id": resource_id,
                "start_datetime": start_datetime,
                "end_datetime": end_datetime,
                "allocated_hours": duration_hours,
            })
            created_plan.action_send()

        created_product = self.env["product.product"].with_company(company).create({
            'name': f'Service Timesheet ({company.name})',
            'type': 'service',
            'company_id': company_id,
            'service_tracking': 'no',
            'list_price': 700.0,
            'service_policy': 'delivered_timesheet',
        })
        pricelist_id = self.env['product.pricelist'].search([("company_id", "=", company_id)], limit=1).id
        product_material = self.env["product.product"].with_company(company).search(
            [("type", "=", "consu")], order='standard_price desc', limit=1)
        self.env["stock.quant"].search([("product_id", "=", product_material.id)]).write({"quantity": 100})

        for project in created_projects:
            sale_order = self.env["sale.order"].create({
                "partner_id": project.partner_id.id,
                "company_id": company_id,
                "project_id": project.id,
                "pricelist_id": pricelist_id,
                "order_line": [(0, 0, {
                    "product_id": created_product.id,
                    "price_unit": created_product.list_price,
                })],
            })
            project.task_ids.write({"sale_line_id": sale_order.order_line.id})
            sale_order._create_invoices()

            tasks = self.env['project.task'].search(
                [('project_id', '=', project.id), ('depend_on_ids', '=', False)], limit=2)
            for task in tasks:
                task.write({
                    'child_ids': [(0, 0, {
                        'name': 'Customer Feedback',
                        'project_id': project.id,
                        'stage_id': task.stage_id.id,
                        'user_ids': [(4, task.user_ids.id)],
                    })],
                })

            purchase_order = self.env["purchase.order"].create({
                "partner_id": project.partner_id.id,
                "company_id": company_id,
                "order_line": [(0, 0, {
                    "product_id": product_material.id,
                    "price_unit": product_material.standard_price,
                })],
                "project_id": project.id,
            })
            purchase_order.button_confirm()

            sale_order.write({
                "order_line": [(0, 0, {
                    "product_id": product_material.id,
                    "price_unit": product_material.list_price,
                })],
            })

        pickings = self.env["stock.picking"].with_company(company).search(
            [("sale_id.project_id", "!=", False)])
        for picking in pickings:
            picking.button_validate()
            if picking.state != "done":
                for move in picking.move_ids:
                    move._action_done()


    def _load_helpdesk_tickets(self):
        company = self.demo_data_id.company_id
        company_id = company.id
        self.env["helpdesk.ticket"].search([('company_id', '=', company_id)]).unlink()
        self.env["helpdesk.team"].search([('company_id', '=', company_id)]).unlink()

        all_company_ids = self.env["res.company"].search([]).ids
        employee_cache = {e.name: e for e in self.env['hr.employee'].search([('company_id', '=', company_id)])}
        user_cache = {u.name: u for u in self.env['res.users'].search([])}
        user_id_emp_cache = {e.user_id.id: e for e in employee_cache.values() if e.user_id}

        with open(os.path.join(self.demo_data_id.data_path, "helpdesk_team.csv"), "r", encoding="utf-8-sig") as csv_file:
            team_rows = list(csv.DictReader(csv_file))

        team_vals_list = []
        for row in team_rows:
            member_user_ids = []
            for member_name in (row.get("member_ids") or "").split(";"):
                member_name = member_name.strip()
                if not member_name:
                    continue
                employee = employee_cache.get(member_name)
                if not employee:
                    continue

                user_partner_ids = employee._get_related_partners()
                user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id
                user_exist = user_cache.get(member_name)
                existing_emp_same_user = user_id_emp_cache.get(user_exist.id) if user_exist else None

                if not user_exist:
                    new_user = self.env["res.users"].create({
                        'company_id': company_id,
                        'company_ids': [(6, 0, all_company_ids)],
                        'name': member_name,
                        'login': member_name,
                        'work_phone': employee.work_phone or False,
                        'work_email': employee.work_email or False,
                        'employee_ids': [(4, employee.id)],
                        'partner_id': user_partner_id,
                    })
                    user_cache[member_name] = new_user
                    member_user_ids.append(new_user.id)
                else:
                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id
                        user_id_emp_cache[user_exist.id] = employee
                    if user_exist.id != 2:
                        user_exist.write({
                            'company_id': company_id,
                            'company_ids': [(6, 0, all_company_ids)],
                            'name': member_name,
                            'login': member_name,
                            'work_phone': employee.work_phone or False,
                            'work_email': employee.work_email or False,
                            'partner_id': user_partner_id,
                        })
                    member_user_ids.append(user_exist.id)

            team_vals_list.append({
                "name": row.get("name"),
                "auto_assignment": True,
                "company_id": company_id,
                "member_ids": [(6, 0, member_user_ids)],
                "description": row.get("description"),
                "use_helpdesk_timesheet": True,
            })

        self.env['res.users'].search([]).write({'company_ids': [(4, company_id)]})

        if team_vals_list:
            self.env["helpdesk.team"].with_company(company).create(team_vals_list)

        with open(os.path.join(self.demo_data_id.data_path, "helpdesk_ticket.csv"), "r", encoding="utf-8-sig") as csv_file:
            ticket_rows = list(csv.DictReader(csv_file))

        team_cache = {t.name: t.id for t in self.env["helpdesk.team"].search([("company_id", "=", company_id)])}
        stage_cache = {s.name: s.id for s in self.env['helpdesk.stage'].search([])}
        partner_cache = {p.name.lower(): p.id for p in self.env["res.partner"].search([])}

        def _priority(n):
            if n < 3: return '0'
            if n < 6: return '1'
            if n < 9: return '2'
            return '3'

        ticket_vals = []
        for n, row in enumerate(ticket_rows):
            user_rec = user_cache.get(row.get("user_id") or "")
            ticket_vals.append({
                "name": row.get("name"),
                "team_id": team_cache.get(row.get("team_id")),
                "user_id": user_rec.id if user_rec else False,
                "description": row.get("description"),
                "priority": _priority(n),
                "stage_id": stage_cache.get(row.get("stage_id")),
                "partner_id": partner_cache.get((row.get("partner_id") or "").lower()),
            })
        if ticket_vals:
            self.env["helpdesk.ticket"].with_company(company).create(ticket_vals)

        with open(os.path.join(self.demo_data_id.data_path, "helpdesk_timesheet.csv"), "r", encoding="utf-8-sig") as csv_file:
            timesheet_rows = list(csv.DictReader(csv_file))

        ticket_cache = {t.name: t for t in self.env["helpdesk.ticket"].search([("company_id", "=", company_id)])}

        timesheet_vals = []
        for row in timesheet_rows:
            ticket = ticket_cache.get(row.get("helpdesk_ticket_id"))
            if not ticket or ticket.stage_id.name in ['New', 'Cancelled']:
                continue
            emp = ticket.user_id.employee_ids.filtered(lambda e: e.company_id == company)
            timesheet_vals.append({
                "name": row.get("name"),
                "helpdesk_ticket_id": ticket.id,
                "project_id": ticket.project_id.id,
                "employee_id": emp.id,
                "unit_amount": row.get("unit_amount"),
                "date": row.get("date"),
            })
        if timesheet_vals:
            self.env["account.analytic.line"].with_company(company).create(timesheet_vals)

        helpdesk_team = self.env['helpdesk.team'].search([('company_id', '=', company_id)])[2]
        team_settings = {
            "use_rating": True,
            "allow_portal_ticket_closing": True,
            "auto_close_ticket": True,
        }
        if self.demo_data_id.stock:
            team_settings.update({
                "use_credit_notes": True,
                "use_giftcards": True,
                "use_coupons": True,
                "use_product_returns": True,
                "use_product_replacements": True,
                "use_product_repairs": True,
                "use_fsm": True,
            })
        helpdesk_team.write(team_settings)


    def _load_event_and_venues(self):
        company = self.demo_data_id.company_id
        company_id = company.id
        venue_tag = self.env["res.partner.category"].with_company(company).create({"name": "venue"})

        with open(os.path.join(self.demo_data_id.data_path, "event_venues.csv"), "r", encoding="utf-8-sig") as csv_file:
            venue_rows = list(csv.DictReader(csv_file))

        country_names = {row.get("country") for row in venue_rows if row.get("country")}
        country_cache = {c.name: c.id for c in self.env["res.country"].search([("name", "in", list(country_names))])}

        venue_vals = [{
            "country_id": country_cache.get(row.get("country")),
            "name": row.get("name"),
            "street": row.get("street"),
            "zip": row.get("zip"),
            "city": row.get("city"),
            "email": row.get("email"),
            "phone": row.get("phone"),
            "is_company": True,
            "company_id": company_id,
            "category_id": venue_tag,
        } for row in venue_rows]
        if venue_vals:
            self.env["res.partner"].with_company(company).create(venue_vals)

        with open(os.path.join(self.demo_data_id.data_path, "events.csv"), "r", encoding="utf-8-sig") as csv_file:
            event_rows = list(csv.DictReader(csv_file))

        all_company_ids = self.env["res.company"].search([]).ids
        employee_cache = {e.name: e for e in self.env['hr.employee'].search([('company_id', '=', company_id)])}
        user_cache = {u.name: u for u in self.env['res.users'].search([])}
        user_id_emp_cache = {e.user_id.id: e for e in employee_cache.values() if e.user_id}
        partner_cache = {p.name.lower(): p.id for p in self.env["res.partner"].with_company(company).search([])}
        event_stage_cache = {s.name: s.id for s in self.env["event.stage"].search([])}

        event_vals_list = []
        for row in event_rows:
            user_name = (row.get("user_id") or "").strip()
            user_rec = user_cache.get(user_name)

            if not user_rec:
                employee = employee_cache.get(user_name)
                if not employee:
                    continue

                user_partner_ids = employee._get_related_partners()
                user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id
                existing_emp_same_user = user_id_emp_cache.get(False)

                new_user = self.env["res.users"].create({
                    'company_id': company_id,
                    'name': user_name,
                    'login': user_name,
                    'company_ids': [(6, 0, all_company_ids)],
                    'work_phone': employee.work_phone or False,
                    'work_email': employee.work_email or False,
                    'employee_ids': [(4, employee.id)],
                    'partner_id': user_partner_id,
                })
                user_cache[user_name] = new_user
                resolved_user_id = new_user.id
            else:
                employee = employee_cache.get(user_name)
                if employee:
                    user_partner_ids = employee._get_related_partners()
                    user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id
                    existing_emp_same_user = user_id_emp_cache.get(user_rec.id)
                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_rec.id
                        user_id_emp_cache[user_rec.id] = employee
                    write_vals = {
                        'company_id': company_id,
                        'company_ids': [(6, 0, all_company_ids)],
                        'work_phone': employee.work_phone or False,
                        'work_email': employee.work_email or False,
                        'partner_id': user_partner_id,
                    }
                    if user_rec.id != 2:
                        write_vals['login'] = user_name
                    user_rec.write(write_vals)
                resolved_user_id = user_rec.id

            event_vals_list.append({
                "name": row.get("name"),
                "date_begin": row.get("date_begin"),
                "date_end": row.get("date_end"),
                "user_id": resolved_user_id,
                "address_id": partner_cache.get((row.get("address_id") or "").lower()),
                "organizer_id": partner_cache.get((row.get("organizer_id") or "").lower()),
                "stage_id": event_stage_cache.get(row.get("stage_id")),
                "lang": "en_US",
            })

        if event_vals_list:
            self.env["event.event"].with_company(company).create(event_vals_list)

        self.env["product.template"].with_company(company).create([{
            "name": f"Event Registration - {product}",
            "type": "service",
            "list_price": 100.0 if product == 'Standard' else 250.0,
            "service_tracking": "event",
        } for product in ['Standard', 'VIP', 'Free']])

        event_products = self.env["product.product"].with_company(company).search(
            [("name", "ilike", "Event Registration")])
        event_product_list = [(p.name.lower(), p.id) for p in event_products]

        event_cache = {e.name: e.id
            for e in self.env["event.event"].with_company(company).search([("company_id", "=", company_id)])}

        with open(os.path.join(self.demo_data_id.data_path, "event_tickets.csv"), "r", encoding="utf-8-sig") as csv_file:
            ticket_rows = list(csv.DictReader(csv_file))

        ticket_vals_list = []
        for row in ticket_rows:
            event_id = event_cache.get(row.get("event_id"))
            if not event_id:
                continue
            ticket_name = row.get("name") or ""
            ticket_name_lower = ticket_name.lower()
            product_id = next((pid for pname, pid in event_product_list
                               if ticket_name_lower in pname and "event" in pname),False,)
            ticket_vals_list.append({
                "name": ticket_name,
                "event_id": event_id,
                "product_id": product_id,
                "price": float(row.get("price") or 0),
                "seats_max": int(row.get("seats_max") or 0),
                "company_id": company_id,
                "start_sale_datetime": row.get("start_sale_datetime"),
                "end_sale_datetime": row.get("end_sale_datetime"),
            })
        if ticket_vals_list:
            self.env["event.event.ticket"].with_company(company).create(ticket_vals_list)

    def _load_event_registrations(self):
        file_path = os.path.join(self.demo_data_id.data_path, "event_registrations.csv")
        company = self.demo_data_id.company_id

        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            rows = list(csv.DictReader(csv_file))
        if not rows:
            return

        partner_cache = {p.name.lower(): p
            for p in self.env["res.partner"].with_company(company).search([("company_id", "in", [company.id, False])])}
        event_cache = {e.name: e for e in self.env["event.event"].with_company(company).search([])}
        ticket_cache = {}
        if event_cache:
            for t in self.env["event.event.ticket"].search([("event_id", "in", [e.id for e in event_cache.values()])]):
                ticket_cache[(t.event_id.id, t.name.lower())] = t

        def find_partner(name):
            if not name:
                return self.env["res.partner"]
            name_lower = name.lower()
            p = partner_cache.get(name_lower)
            if p:
                return p
            return next((p for n, p in partner_cache.items() if name_lower in n),self.env["res.partner"],)

        def find_ticket(event_id, ticket_name):
            if not ticket_name or not event_id:
                return self.env["event.event.ticket"]
            ticket_name_lower = ticket_name.lower()
            t = ticket_cache.get((event_id, ticket_name_lower))
            if t:
                return t
            return next((t for (eid, tname), t in ticket_cache.items()
                 if eid == event_id and ticket_name_lower in tname),self.env["event.event.ticket"],)

        reg_vals_list = []
        reg_meta = []
        for row in rows:
            partner = find_partner(row.get("partner_id"))
            event = event_cache.get(row.get("event_id", ""))
            if not event:
                continue
            ticket = find_ticket(event.id, row.get("event_ticket_id"))
            reg_vals_list.append({
                "event_id": event.id,
                "partner_id": partner.id or False,
                "event_ticket_id": ticket.id or False,
                "company_name": partner.parent_id.name or False,
            })
            reg_meta.append((partner, event, ticket))

        if not reg_vals_list:
            return

        registrations = self.env["event.registration"].with_company(company).create(reg_vals_list)

        SaleOrder = self.env["sale.order"].with_company(company)
        for reg, (partner, event, ticket) in zip(registrations, reg_meta):
            stage_name = event.stage_id.name
            if stage_name in ("Announced", "Ended"):
                sale_order = SaleOrder.create({
                    "partner_id": partner.id,
                    "order_line": [(0, 0, {
                        "name": "Event Registration",
                        "product_id": ticket.product_id.id,
                        "product_uom_qty": 1,
                        "price_unit": ticket.price,
                        "event_id": event.id,
                        "event_ticket_id": ticket.id,
                    })],
                })
                reg.sale_order_id = sale_order.id
            if stage_name == "Ended":
                reg.action_set_done()


    def _load_marketing_campaigns(self):
        company = self.demo_data_id.company_id
        admin_user = self.env["res.users"].browse(2)
        if not admin_user.email:
            admin_employee = admin_user.employee_ids.filtered(lambda l: l.company_id == company)
            if admin_employee:
                admin_user.write({'email': admin_employee.work_email, 'phone': admin_employee.work_phone})
                admin_user.partner_id.write({'email': admin_employee.work_email, 'phone': admin_employee.work_phone})
            else:
                admin_user.write({'email': 'test@user.com'})

        MC = self.env['marketing.campaign'].with_company(company)
        MC.search([]).unlink()

        file_path = os.path.join(self.demo_data_id.data_path, "mailing_list.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            rows = list(csv.DictReader(csv_file))

        if rows:
            country_names = {row.get("country_id") for row in rows if row.get("country_id")}
            country_cache = {c.name: c.id
                             for c in self.env["res.country"].search([("name", "in", list(country_names))])}
            mailing_vals = [{
                "name": row.get("name"),
                "email": row.get("email"),
                "company_name": row.get("company_name"),
                "country_id": country_cache.get(row.get("country_id")),
            } for row in rows]
            self.env["mailing.contact"].with_company(company).create(mailing_vals)

        MC._get_marketing_template_commercial_prospection_values()
        MC._get_marketing_template_prioritize_hot_leads_values()
        MC._get_marketing_template_double_opt_in_values()
        MC._get_marketing_template_purchase_followup()

        all_campaigns = MC.search([])
        all_campaigns.domain = []

        campaigns = all_campaigns[:3]
        campaigns.action_start_campaign()
        campaigns.sync_participants()


    def _load_articles(self):
        data_path = self.demo_data_id.data_path
        company = self.demo_data_id.company_id
        demo_name = self.demo_data_id.name

        keep_names = {
            f"sales process ({demo_name.lower()})",
            f"supply chain process ({demo_name.lower()})",
            f"hr process ({demo_name.lower()})",
            f"administrative process ({demo_name.lower()})",
        }

        all_articles = self.env["knowledge.article"].with_company(company).search([])
        to_delete = all_articles.filtered(lambda r: (r.name or "").lower() in keep_names)
        to_delete.unlink()

        images_to_delete = {
            "sales_process.jpg",
            "hr_process.jpg",
            "supply_chain_process.jpg",
            "administrative_process.jpg",
        }

        for image_name in images_to_delete:
            image_path = os.path.join(data_path, image_name)
            if os.path.isfile(image_path):
                try:
                    os.remove(image_path)
                    _logger.info("Deleted image", image_path)
                except Exception as e:
                    _logger.warning("Could not delete image ", image_path, e)

        articles_with_images = {
            "sales process",
            "supply chain process",
            "hr process",
            "administrative process",
        }

        image_agent = ImageAgent(self.demo_data_id.api_key_id.name)
        image_agent.get_agent()

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        Attachment = self.env["ir.attachment"].with_company(company)

        def _generate_image(safe_name, image_suffix, prompt):
            filename = f"{safe_name}_{image_suffix}.jpg"
            image_path = os.path.join(data_path, filename)
            if not os.path.isfile(image_path):
                image_agent.save_image(prompt, image_path)
            if os.path.isfile(image_path):
                with open(image_path, "rb") as image:
                    attachment = Attachment.create({
                        "name": filename,
                        "datas": base64.b64encode(image.read()),
                        "res_model": "knowledge.article",
                        "mimetype": "image/jpeg",
                        "type": "binary",
                    })
                    if attachment and attachment.checksum:
                        return f"{base_url}/{attachment.image_src}"
            return False

        file_path = os.path.join(data_path, "knowledge_articles.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            rows = list(csv.DictReader(csv_file))

        article_vals = []
        for row in rows:
            article = row.get("name")
            safe_name = article.lower().replace(" ", "_")
            image_url_1 = False
            if article.lower() in articles_with_images:
                prompt_1 = (image_prompts.ARTICLE_IMAGES_PROMPT
                            .replace("__name__", demo_name)
                            .replace("__article_title__", f"{article} illustration")
                            )
                image_url_1 = _generate_image(safe_name, "1", prompt_1)
            body_html = f"<h1>{row.get('title') or row.get('name')}</h1>"
            if image_url_1:
                body_html += f"<p><img src='{image_url_1}' class='img img-fluid o_we_custom_image'></p>"
            body_html += f"<p>{row.get('intro')}</p></br><p>{row.get('body')}</p></br>"
            body_html += f"<p>{row.get('conclusion')}</p>"

            article_vals.append({
                "name": f"{article} ({demo_name.upper()})",
                "body": body_html,
                "is_article_visible_by_everyone": True,
            })

        if article_vals:
            self.env["knowledge.article"].with_company(company).create(article_vals)


        all_articles = self.env["knowledge.article"].with_company(company).search([])
        to_delete = all_articles.filtered(
            lambda r: (r.name or "").lower() not in keep_names
        )
        to_delete.unlink()

    def _get_dates_between(self, date_from, date_to):
        dates = []
        current = date_from.date()
        end = date_to.date()
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)
        return dates

    def _has_overlapping_leave(self, employee, date_from, date_to):
        domain = [
            ('employee_id', '=', employee.id),
            ('state', 'not in', ['cancel', 'refuse']),
            ('date_from', '<', date_to),
            ('date_to', '>', date_from),
            ('company_id', '=', self.demo_data_id.company_id.id),
        ]
        return bool(self.env['hr.leave'].search_count(domain))


    def _is_non_working_day(self, employee, day, is_single_day_leave=False):
        calendar = employee.resource_calendar_id
        day_dt = fields.Datetime.to_datetime(day)

        weekday = day_dt.weekday()
        working_days = set(calendar.attendance_ids.mapped('dayofweek'))
        if str(weekday) not in working_days:
            return True

        public_holiday_domain = [
            ('resource_id', '=', False),
            ('calendar_id', 'in', [calendar.id, False]),
            ('date_from', '<=', day_dt),
            ('date_to', '>=', day_dt),
        ]
        is_public_holiday = self.env['resource.calendar.leaves'].search_count(public_holiday_domain)

        if is_public_holiday:
            return True

        leave_domain = [
            ('calendar_id', 'in', [calendar.id, False]),
            ('date_from', '<=', day_dt),
            ('date_to', '>=', day_dt),
        ]
        return bool(self.env['resource.calendar.leaves'].search_count(leave_domain))

    def _load_time_off_requests(self):
        company = self.demo_data_id.company_id
        company_id = company.id

        current_employee = self.env["hr.employee"].search(
            [("name", "ilike", self.env.user.name), ("company_id", "=", company_id)], limit=1)
        if current_employee and not current_employee.user_id:
            current_employee.write({"user_id": self.env.user.id})

        hr_leaves = self.env["hr.leave"].search([("company_id", "=", company_id)])
        approved_leaves = hr_leaves.filtered(lambda l: l.state == "approved")
        if approved_leaves:
            approved_leaves.action_refuse()
        hr_leaves.unlink()

        allocations = self.env["hr.leave.allocation"].search([("employee_company_id", "=", company_id)])
        validated_allocs = allocations.filtered(lambda a: a.state not in  ["confirm","refuse"])
        if validated_allocs:
            validated_allocs.action_refuse()
        allocations.unlink()

        self.env["hr.leave.mandatory.day"].with_company(company).search([("company_id", "=", company_id)]).unlink()
        self.env["hr.leave.accrual.plan"].with_company(company).search([]).unlink()
        self.env["resource.calendar.leaves"].with_company(company).search([("company_id", "=", company_id)]).unlink()
        if self.demo_data_id.project:
            self.env["account.analytic.line"].with_company(company).search([("name", "ilike", "Time Off"),
                    ("global_leave_id", "=", False), ("holiday_id", "=", False)]).unlink()

        file_path = os.path.join(self.demo_data_id.data_path, "mandate_days.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            mandate_rows = list(csv.DictReader(csv_file))
        if mandate_rows:
            mandate_vals = []
            for row in mandate_rows:
                vals = dict(row)
                date_val = vals.pop("date")
                vals["start_date"] = date_val
                vals["end_date"] = date_val
                mandate_vals.append(vals)
            self.env["hr.leave.mandatory.day"].with_company(company).create(mandate_vals)

        RCL = self.env["resource.calendar.leaves"].with_company(company)
        file_path = os.path.join(self.demo_data_id.data_path, "public_holidays.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                base_date = datetime.strptime(row["date_from"], "%Y-%m-%d %H:%M:%S")
                date_from = datetime.combine(base_date.date(), time.min)
                date_to = date_from + timedelta(hours=12)
                date_from_str = Datetime.to_string(date_from)
                date_to_str = Datetime.to_string(date_to)
                if RCL.search(
                    [('date_from', '<', date_to_str), ('date_to', '>', date_from_str),
                     ('company_id', '=', company_id)], limit=1):
                    continue
                row["date_from"] = date_from_str
                row["date_to"] = date_to_str
                RCL.create(row)

        HLT = self.env["hr.leave.type"].with_company(company)
        accrual_leave_type = HLT.search([("name", "=", "Accrual Plan Leave")], limit=1)
        if not accrual_leave_type:
            accrual_leave_type = HLT.create({
                "name": "Accrual Plan Leave",
                "requires_allocation": True,
                "leave_validation_type": "no_validation",
                "allocation_validation_type": "no_validation",
            })

        self.env["hr.leave.accrual.plan"].with_company(company).search([]).unlink()
        accrual_plan = self.env["hr.leave.accrual.plan"].with_company(company).create({
            "name": "Standard Accrual Plan",
            "company_id": company_id,
        })
        self.env["hr.leave.accrual.level"].with_company(company).create({
            "accrual_plan_id": accrual_plan.id,
            "added_value": 2,
            "frequency": "monthly",
        })

        current_year = fields.Date.today().year
        accrual_leave = self.env["hr.leave.allocation"].with_company(company).create({
            "name": "Initial Accrual Allocation",
            "holiday_status_id": accrual_leave_type.id,
            "allocation_type": "accrual",
            "accrual_plan_id": accrual_plan.id,
            "number_of_days": 22,
            "employee_id": current_employee.id,
            "date_from": f"{current_year}-01-01",
            "date_to": f"{current_year}-12-31",
        })
        accrual_leave._compute_description()
        HLT.search([]).write({"leave_validation_type": "hr"})

        employee_cache = {emp.name: emp for emp in self.env["hr.employee"].search([("company_id", "=", company_id)])}
        leave_type_cache = {lt.name: lt for lt in HLT.search([])}
        HLA = self.env["hr.leave.allocation"].with_company(company)
        file_path = os.path.join(self.demo_data_id.data_path, "time_off_allocation.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            for allocation_row in csv.DictReader(csv_file):
                employee = employee_cache.get(allocation_row["employee_id"])
                if not employee:
                    continue
                leave_type = leave_type_cache.get(allocation_row["holiday_status_id"])
                if not leave_type:
                    continue
                allocation_row["employee_id"] = employee.id
                allocation_row["holiday_status_id"] = leave_type.id
                allocation_row["date_from"] = f"{current_year}-01-01"
                created_allocation = HLA.create(allocation_row)
                created_allocation.action_approve()

        today = fields.Date.today()
        HLeave = self.env["hr.leave"].with_company(company)
        file_path = os.path.join(self.demo_data_id.data_path, "time_off_requests.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                employee = employee_cache.get(row["employee_id"])
                if not employee:
                    continue
                leave_type = leave_type_cache.get(row["holiday_status_id"])
                if not leave_type:
                    continue

                leave_data = leave_type.get_allocation_data(employee, row['request_date_from'])

                if leave_type.requires_allocation != 'no' and not leave_data.get(employee):
                    continue

                if leave_type.requires_allocation != 'no':
                    allocation_info = leave_data[employee][0][1]
                    remaining = allocation_info.get('virtual_remaining_leaves', 0.0)
                    max_excess = leave_type.max_allowed_negative
                    if leave_data[employee] and remaining < -max_excess:
                        continue
                    if not leave_type.allows_negative and remaining <= 0:
                        continue
                    if leave_type.allows_negative and remaining < -leave_type.max_allowed_negative:
                        continue

                row["employee_id"] = employee.id
                row["holiday_status_id"] = leave_type.id

                date_from = fields.Datetime.from_string(row["request_date_from"])
                date_to = fields.Datetime.from_string(row["request_date_to"])

                leave_dates = self._get_dates_between(date_from, date_to)
                total_days = len(leave_dates)
                non_working_count = sum(1 for d in leave_dates if self._is_non_working_day(employee, d))

                if total_days == 1 and non_working_count > 0:
                    continue
                if total_days == 2 and non_working_count >= 2:
                    continue

                if self._has_overlapping_leave(employee, date_from, date_to):
                    continue

                created_leave = HLeave.create(row)
                leave_date = created_leave.date_from.date()
                if (leave_date < today.replace(day=1)
                        and created_leave.holiday_status_id.leave_validation_type != 'no_validation'):
                    created_leave.action_approve()

    def _load_recruitment_data(self):
        company = self.demo_data_id.company_id
        company_id = company.id
        all_company_ids = self.env["res.company"].search([]).ids

        user_cache = {u.name: u for u in self.env["res.users"].search([])}
        user_name_lower_list = [(n.lower(), u) for n, u in user_cache.items()]

        for employee in self.env["hr.employee"].search([("company_id", "=", company_id)]):
            if user_cache.get(employee.name):
                continue
            user_partner_ids = employee._get_related_partners()
            user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id
            new_user = self.env["res.users"].create({
                "company_id": company_id,
                "company_ids": [(6, 0, all_company_ids)],
                "name": employee.name,
                "login": employee.name,
                "work_phone": employee.work_phone or False,
                "work_email": employee.work_email or False,
                "employee_ids": [(4, employee.id)],
                "partner_id": user_partner_id,
            })
            user_cache[employee.name] = new_user

        dept_cache = {d.name: d.id
                      for d in self.env["hr.department"].with_company(company).search([("company_id", "=", company_id)])}
        degree_cache = {d.name: d.id for d in self.env["hr.recruitment.degree"].search([])}
        contract_type_cache = {c.name: c.id for c in self.env["hr.contract.type"].with_company(company).search([])}

        file_path = os.path.join(self.demo_data_id.data_path, "job_positions.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            job_rows = list(csv.DictReader(csv_file))

        job_vals_list = []
        for row in job_rows:
            interviewer_ids = []
            for name in (row.get("interviewer_ids") or "").split(";"):
                name = name.strip()
                if not name:
                    continue
                user = user_cache.get(name) or next((u for n, u in user_name_lower_list if name.lower() in n), None)
                if user:
                    interviewer_ids.append(user.id)

            user_rec = user_cache.get(row.get("user_id") or "")
            job_vals_list.append({
                "name": row.get("name"),
                "department_id": dept_cache.get(row.get("department_id")),
                "description": row.get("description"),
                "user_id": user_rec.id if user_rec else False,
                "interviewer_ids": [(6, 0, interviewer_ids)],
                "expected_degree": degree_cache.get(row.get("expected_degree")),
                "contract_type_id": contract_type_cache.get(row.get("contract_type_id")),
                "no_of_recruitment": row.get("no_of_recruitment"),
                "company_id": company_id,
            })

        HrJob = self.env["hr.job"].with_company(company)
        created_jobs = HrJob.create(job_vals_list) if job_vals_list else HrJob
        job_cache = {j.name: j for j in created_jobs}

        stage_new = self.env["hr.recruitment.stage"].with_company(company).search([("name", "=", "New")], limit=1)
        stage_cache = {s.name: s.id for s in self.env["hr.recruitment.stage"].with_company(company).search([])}

        file_path = os.path.join(self.demo_data_id.data_path, "job_applications.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            applicant_rows = list(csv.DictReader(csv_file))

        Applicant = self.env["hr.applicant"].with_company(company)
        applicant_vals_list = []
        for applicant_row in applicant_rows:
            job = job_cache.get(applicant_row.get("job_id"))
            user_rec = user_cache.get(applicant_row.get("user_id") or "")
            applicant_vals_list.append({
                "partner_name": applicant_row.get("partner_name"),
                "email_from": applicant_row.get("email_from"),
                "partner_phone": applicant_row.get("partner_phone"),
                "job_id": job.id if job else False,
                "user_id": user_rec.id if user_rec else False,
                "stage_id": stage_new.id,
                "type_id": degree_cache.get(applicant_row.get("type_id")),
                "interviewer_ids": job.interviewer_ids if job else self.env["res.users"],
                "availability": applicant_row.get("availability"),
                "applicant_notes": applicant_row.get("applicant_notes"),
                "salary_expected": applicant_row.get("salary_expected"),
                "salary_proposed": applicant_row.get("salary_proposed"),
                "priority": applicant_row.get("priority"),
                "company_id": company_id,
            })

        created_applicants = Applicant.create(applicant_vals_list)

        stage_to_app_ids = {}
        for applicant, row in zip(created_applicants, applicant_rows):
            final_stage_id = stage_cache.get(row.get("stage_id"))
            if final_stage_id:
                stage_to_app_ids.setdefault(final_stage_id, []).append(applicant.id)
        for stage_id, app_ids in stage_to_app_ids.items():
            self.env["hr.applicant"].browse(app_ids).write({"stage_id": stage_id})


    def _load_website_data(self):
        vals = {
            "target_url": self.demo_data_id.website_url,
            "import_products": False,
        }
        self.env["website"].search([],limit=1).write({"company_id": self.demo_data_id.company_id.id})
        website_request = self.env["website_generator.request"].with_company(self.demo_data_id.company_id).create(vals)
        website_request.with_context(from_cron=True).get_result_waiting_requests()
        self.env.ref("website_generator.cron_get_result").write({"interval_number": 1})

    function_dict = {
        "General Information": _load_general_information,
        "Background Image": _load_background,
        "Products": _load_products,
        "Products Pictures": _load_product_pictures,
        "Customers and Suppliers": _load_partners,
        "Customers and Suppliers: Child Contacts": _load_child_contacts,
        "Analytic Accounts": _load_analytic_tags,
        "Customer and Supplier bills": _load_bills,
        "Assets": _load_data_asset,
        "Generate Invoice PDF": _load_data_pdf,
        "CRM": _load_data_crm,
        "Inventory": _load_data_stock,
        "Sales": _load_data_sales,
        "Generate Header PDF": _load_header_pdf,
        "Generate Footer PDF":_load_footer_pdf,
        "Purchase": _load_data_purchase,
        "Employees": _load_data_hr,
        "Employees Pictures": _load_pictures_hr,
        "Manufacture": _load_mrp_bom,
        "Manufacture: Bills of Materials": _load_mrp_components,
        "Manufacture: Work Centers": _load_work_centers,
        "Manufacture: BOM Operations": _load_bom_operations,
        "Manufacturing Orders": _load_manufacturing_orders,
        "Manufacturing: BOM Operation Instructions": _load_bom_instructions,
        "Point of Sale: Products": _load_pos_data,
        "Point of Sale: Orders": _load_pos_orders,
        "Field Service": _load_field_service_data,
        "Field Service: Worksheets": _load_field_service_worksheets,
        "Project and Tasks": _load_project_and_tasks,
        "Helpdesk Tickets": _load_helpdesk_tickets,
        "Event: Events and Venues": _load_event_and_venues,
        "Event: Registrations and Ticket sales": _load_event_registrations,
        "Marketing Automation: Campaigns": _load_marketing_campaigns,
        "Knowledge: Articles": _load_articles,
        "Time Off: Requests and Allocations": _load_time_off_requests,
        "Recruitment: Job Positions and Applications": _load_recruitment_data,
        "Website": _load_website_data,
    }

    def load_data(self):
        _logger.info("Loading data for", self.name)
        if self.function_dict[self.name]:
            self.with_company(self.demo_data_id.company_id).function_dict[self.name](self)
        else:
            self.state = "failed"
            raise UserError("Tried generating data for a non-supported module")
        _logger.info("Finished loading data for", self.name)

    def exec_action(self):
        if self.nb_tries == 0 and self.name == "General Information" and self.demo_data_id.ai_info:
            self.state = "ok"
            self.nb_tries = 1
            return True

        if self.name == "Website" and self.state == "processing":
            request = self.env["website_generator.request"].search([("target_url", "ilike", self.demo_data_id.website_url)])
            if request.status == "done":
                self.state = "ok"
                self.logs = request.status
                return True
            else:
                self.state = "processing"
                if request.status not in ['waiting', 'error_request_still_processing']:
                    self.logs = request.status
                    self.state = "skipped"

                if not self.env.ref("website_generator.cron_get_result").active:
                    self.env.ref("website_generator.cron_get_result").write({"active": True})
                return True

        if self.state == "ok" and self.name == "Inventory":
            if (self.env["ir.module.module"].search([("name", "=", "purchase")], limit=1).state in
                    ["installed","to_upgrade","to_remove"]):
                company = self.demo_data_id.company_id
                replenishments = self.env["stock.warehouse.orderpoint"].with_company(company).search(
                    [("company_id", "=", company.id)])

                replenishments.product_id.write({
                    "seller_ids": [(0, 0, {
                        "partner_id": company.partner_id.id,
                        "min_qty": 1,
                        "price": 0.0,
                    })]
                })


        is_done = False
        n = 0
        self.state = "processing"
        while (not is_done) and n <= 3:
            try:
                self.generate_data(
                    self.demo_data_id.get_values_dict(),
                    self.demo_data_id.api_key_id.name,
                )
                self.load_data()
                is_done = True
            except Exception as e:
                self.logs = e
                _logger.error(e)
            n += 1
        self.nb_tries += n
        if not is_done:
            self.state = "failed"
            return False
        self.state = "ok"
        if self.name == "Website" and self.nb_tries == 1:
            self.state = "processing"
        return True

