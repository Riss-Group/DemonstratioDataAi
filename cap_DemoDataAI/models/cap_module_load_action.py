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
from mistralai import Mistral
from mistralai.models import ToolFileChunk
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
from requests.exceptions import RequestException

_logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)


MISTRAL_SMALL = "mistral-small-latest"
MISTRAL_MEDIUM = "mistral-medium-latest"


class ImageAgent:
    def __init__(self, api_key: str):
        self.client = Mistral(api_key)

    def gen_image(self, prompt: str):
        response = self.client.beta.conversations.start(
            agent_id=self.image_agent.id, inputs=prompt
        )
        for _, chunk in enumerate(response.outputs[-1].content):
            if isinstance(chunk, ToolFileChunk):
                return self.client.files.download(file_id=chunk.file_id).read()
        return None

    def save_image(self, prompt: str, path_name: str):
        file_byte = self.gen_image(prompt)
        with open(path_name, "wb") as image_file:
            image_file.write(file_byte)

    def create_agent(self):
        self.image_agent = self.client.beta.agents.create(
            model=MISTRAL_MEDIUM,
            name="Image generation API",
            description="Agent used to create images for the Demo Data module",
            instructions="Use the image generation tool to create the images.",
            tools=[{"type": "image_generation"}],
            completion_args={
                "temperature": 0.3,
            },
        )

    def get_agent(self):
        agent_list = self.client.beta.agents.list()
        for agent in agent_list:
            if agent.name == "Image generation API":
                self.image_agent = self.client.beta.agents.get(agent_id=agent.id)
                return
        _logger.warning("No agent found for the name Image generation API")
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
        self.generate_data(
            self.demo_data_id.get_values_dict(), self.demo_data_id.api_key_id.name
        )
        self.load_data()
        self.state = "ok"

    @api.onchange("prompt")
    def _on_change_prompt(self):
        self.state = "in_line"
        self.demo_data_id.generation_in_progress = True

    def _normalize_llm_csv(self,csv_text: str) -> str:
        """
        Convert single-quoted CSV fields to double-quoted CSV safely.
        Keeps apostrophes inside words untouched.
        """
        pattern = r"(^|,)\s*'([^']*)'\s*(?=,|$)"

        def repl(match):
            prefix = match.group(1)
            value = match.group(2)
            return f'{prefix}"{value}"'

        return re.sub(pattern, repl, csv_text, flags=re.MULTILINE)

    def _fix_csv_commas(self, csv_text: str) -> str:
        """
        Removes spaces after commas that break CSV parsing.
        """
        csv_text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
        csv_text = re.sub(r',\s+', ',', csv_text)

        return csv_text.strip()

    def generate_data(self, dict_fill_in: dict, api_key: str):
        if not self.file_to_load or not self.prompt:
            _logger.info("No generation for this action")
            return
        _logger.info("Starting generation for", self.name)
        for element in dict_fill_in:
            self.prompt = self.prompt.replace(element, dict_fill_in[element])

        if "account.asset" in self.prompt:
            accounts = self.env["account.account"].search_read(
                [("company_ids", "parent_of", self.demo_data_id.company_id.id),
		         ('account_type','in',['asset_current','asset_non_current','asset_fixed','expense'])],
                ["name", "account_type"],
            )
            self.prompt = self.prompt + str(accounts)

        result = cap_api_call.mistral_api_call(
            MISTRAL_SMALL, self.prompt, api_key, self.demo_data_id.lang
        )
        text = result.choices[0].message.content
        # print("text",text)
        files_to_load = [f.strip() for f in self.file_to_load.split(";")]

        is_csv = any(f.lower().endswith(".csv") for f in files_to_load)
        base_path = Path(self.demo_data_id.data_path)

        if not is_csv:
            for file_name in files_to_load:
                (base_path / file_name).write_text(text, encoding="utf-8-sig")
            return

        regex_pattern = r"```csv\s*(.+?)\s*```"
        matches = re.findall(regex_pattern, text, flags=re.DOTALL)

        if not matches:
            raise ValueError("No CSV blocks found in LLM response")

        if len(matches) < len(files_to_load):
            raise ValueError(
                f"CSV blocks mismatch: expected {len(files_to_load)}, got {len(matches)}"
            )

        for file_name, csv_content in zip(files_to_load, matches):
            csv_content = re.sub(r"^```csv\s*", "", csv_content.strip(), flags=re.IGNORECASE)
            cleaned_csv = self._normalize_llm_csv(csv_content.strip())
            cleaned_csv = self._fix_csv_commas(cleaned_csv)
            (base_path / file_name).write_text(
                cleaned_csv,
                encoding="utf-8-sig"
            )
        _logger.info("Finished generation for", self.name)

    def _load_general_information(self):
        """
        Useful to prevent 'General Information' action from crashing
        """
        current_user = self.env.user
        if current_user:
            current_user.email = f"{self.demo_data_id.name.lower()}@test.com"
            current_user.phone = "+1234567890"
            try:
                current_user.work_email = current_user.email
                current_user.work_phone = current_user.phone

            except Exception:
                pass



    def _load_background(self):
        image_agent = ImageAgent(self.demo_data_id.api_key_id.name)
        with open(
            os.path.join(self.demo_data_id.data_path, "background_image.txt"),
            "r",
            encoding="utf-8-sig",
        ) as file:
            prompt = file.read()
        image_agent.get_agent()
        image_agent.save_image(
            prompt, os.path.join(self.demo_data_id.data_path, "background_image.jpg")
        )
        with open(
            os.path.join(self.demo_data_id.data_path, "background_image.jpg"), "rb"
        ) as image:
            self.demo_data_id.company_id.background_image = base64.b64encode(
                image.read()
            )

    def _load_products(self):
        def _load_product(file_name):
            is_stock: bool = bool(self.env["ir.module.module"].search([("name", "=", "stock")], limit=1).state in ["installed","to_remove",
                                                                                                 "to_upgrade"] and self.demo_data_id.stock)
            with open(os.path.join(self.demo_data_id.data_path, file_name),
                mode="r",encoding="utf-8-sig",) as csv_file:
                csv_reader = csv.DictReader(csv_file)
                for row in csv_reader:
                    row["list_price"] = float(row.get("list_price").replace(",",""))
                    vals = {"name": row.get("name"),
                            "default_code": row.get("default_code"),
                            "list_price": row.get("list_price"),
                            "type": row.get("type"),
                            "standard_price": row.get("standard_price"),}

                    if "invoice_policy" in self.env["product.product"]._fields:
                        vals["invoice_policy"] = "delivery"
                    if self.demo_data_id.bool_company_restricted:
                        vals["company_id"] = self.demo_data_id.company_id.id
                    if row["type"] == "consu" and is_stock:
                        vals["is_storable"] = True

                    self.env["product.template"].with_company(
                        self.demo_data_id.company_id
                    ).create(vals)

        _load_product("customer_products.csv")
        _load_product("supplier_products.csv")

    def _partner_lang(self,country, overwrite=True):
        Lang = self.env['res.lang']
        Module = self.env['ir.module.module']
        french_countries = {'France', 'Canada', 'Luxembourg', 'Madagascar'}

        if country not in french_countries:
            return "en_US"

        lang_code = "fr_FR"

        lang = Lang.with_context(active_test=False).search(["|", "|", ("name", "ilike", lang_code),
                            ("code", "ilike", lang_code),
                            ("iso_code", "ilike", lang_code)], limit=1)

        if not lang:
            return "en_US"

        if lang and not lang.active:
            lang.action_unarchive()
            lang.active = True

            installed_modules = Module.search([('state', '=', 'installed')])
            installed_modules._update_translations(lang_code, overwrite)


        return lang_code

    def _load_partners(self):
        customer_tag = (
            self.env["res.partner.category"]
            .with_company(self.demo_data_id.company_id)
            .create({"name": "customer"})
        )
        with open(
            os.path.join(self.demo_data_id.data_path, "customers.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                country = self.env["res.country"].search(
                    [("name", "=", row.get("country"))],limit=1)
                state = self.env["res.country.state"].search(
                    [("name", "=", row.get("state")), ("country_id", "=", country.id)], limit=1)

                vals = {
                    "name": row.get("name"),
                    "email": row.get("email"),
                    "phone": row.get("phone"),
                    "street2": row.get("street") + ', ' + row.get("street2"),
                    "city": row.get("city"),
                    "zip": row.get("zip"),
                    "website": row.get("website"),
                    "customer_rank": 1,
                    "is_company": True,
                    "category_id": customer_tag,
                    "country_id": country.id or False,
                    "state_id": state.id or False,
                    "lang": self._partner_lang(country=country.name, overwrite=True),
                    "followup_responsible_id": self.env.user.id,
                }

                if self.demo_data_id.bool_company_restricted:
                    vals["company_id"] = self.demo_data_id.company_id.id

                cust_partner = self.env["res.partner"].with_company(
                    self.demo_data_id.company_id
                ).create(vals)

                backoff = 3
                max_retries = 3

                for attempt in range(max_retries):
                    try:
                        cust_partner.geo_localize()
                        break

                    except RequestException:
                        sleep_time.sleep(backoff)
                        backoff *= 2

                    except Exception:
                        sleep_time.sleep(backoff)
                        backoff *= 2

        supplier_tag = (
            self.env["res.partner.category"]
            .with_company(self.demo_data_id.company_id)
            .create({"name": "supplier"})
        )
        with open(
            os.path.join(self.demo_data_id.data_path, "suppliers.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                country = self.env["res.country"].search(
                    [("name", "=", row.get("country"))], limit=1)
                state = self.env["res.country.state"].search(
                    [("name", "=", row.get("state")), ("country_id", "=", country.id)], limit=1)

                vals = {
                    "name": row.get("name"),
                    "email": row.get("email"),
                    "phone": row.get("phone"),
                    "street2": row.get("street") + ', ' + row.get("street2"),
                    "city": row.get("city"),
                    "zip": row.get("zip"),
                    "website": row.get("website"),
                    "supplier_rank": 1,
                    "is_company": True,
                    "category_id": supplier_tag,
                    "country_id": country.id or False,
                    "state_id": state.id or False,
                    "lang": self._partner_lang(country=country.name, overwrite=True),
                    "followup_responsible_id": self.env.user.id,
                }

                if self.demo_data_id.bool_company_restricted:
                    vals["company_id"] = self.demo_data_id.company_id.id

                supp_partner = self.env["res.partner"].with_company(
                    self.demo_data_id.company_id
                ).create(vals)

                backoff = 3
                max_retries = 3

                for attempt in range(max_retries):
                    try:
                        supp_partner.geo_localize()
                        break

                    except RequestException:
                        sleep_time.sleep(backoff)
                        backoff *= 2

                    except Exception:
                        sleep_time.sleep(backoff)
                        backoff *= 2

        with open(os.path.join(self.demo_data_id.data_path, "log_chatter.csv"), mode="r",
                  encoding="utf-8-sig", ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            log_list = []
            contacts = self.env["res.partner"].with_company(self.demo_data_id.company_id).search(
                ["|", ("category_id.name", "=", "supplier"), ("category_id.name", "=", "customer")])

            now = self.demo_data_id.demo_date
            three_months_ago = now - timedelta(days=90)
            log_sets = defaultdict(list)
            for row in csv_reader:
                log_sets[int(row["set"])].append(row)
                for s in log_sets:
                    log_sets[s] = sorted(log_sets[s], key=lambda x: int(x["sequence"]))

                log_list.append(row)

            for contact in contacts:
                thread_set = random.choice(list(log_sets.keys()))
                thread_logs = log_sets[thread_set]

                total_seconds = int((now - three_months_ago).total_seconds())

                # generate chronological timestamps for the thread
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

                    # set author depending on sender
                    if log.get("from") == "customer" or log.get("from") == "supplier":
                        values.update({
                            "author_id": contact.id,
                            "email_from": f'"{contact.name}" <{contact.email}>',
                            "reply_to": f'"{contact.name}" <{contact.email}>',
                        })
                    else:
                        values.update({
                            "author_id": self.env.user.partner_id.id,
                            "email_from": self.env.user.partner_id.email,
                            "reply_to": self.env.user.partner_id.email,
                        })

                    self.env["mail.message"].create(values)

    def _load_child_contacts(self):
        Partner = self.env["res.partner"].with_company(self.demo_data_id.company_id)
        admin_user = self.env["res.users"].search([("id", "=", 2)])
        company_partner_ids = self.env["res.company"].sudo().search([]).mapped("partner_id.id")
        excluded_partner_ids = company_partner_ids + [admin_user.partner_id.id]

        parents = Partner.search([
            ("is_company", "=", True),
            ("id", "not in", excluded_partner_ids)
        ])
        if not parents:
            return

        parent_list = parents.ids.copy()
        random.shuffle(parent_list)

        parent_child_count = {pid: 0 for pid in parent_list}

        with open(
            os.path.join(self.demo_data_id.data_path, "child_contacts.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)

            for row in csv_reader:
                available_parents = [
                    pid for pid, count in parent_child_count.items() if count < 2
                ]
                if not available_parents:
                    break

                selected_parent_id = random.choice(available_parents)
                parent = Partner.browse(selected_parent_id)
                parent_child_count[selected_parent_id] += 1

                child_name = str(row.get("name", "")).strip()
                parent_name = str(parent.name or "").strip()

                local_part = ""
                if child_name:
                    local_part = re.sub(r"[^a-zA-Z0-9 ]", "", child_name).lower().replace(" ", ".")

                email_domain = None
                if parent.email and "@" in parent.email:
                    email_domain = parent.email.split("@")[-1].strip().lower()

                if not email_domain:
                    domain_base = re.sub(r"[^a-zA-Z0-9 ]", "", parent_name).lower().replace(" ", "")
                    email_domain = f"{domain_base}.com"

                child_email = ""
                if local_part:
                    child_email = f"{local_part}@{email_domain}"

                values = {
                    "name": row.get("name"),
                    "email": child_email,
                    "phone": row.get("phone"),
                    "parent_id": parent.id,
                }
                if self.demo_data_id.bool_company_restricted:
                    values["company_id"] = self.demo_data_id.company_id.id

                Partner.with_company(self.demo_data_id.company_id.id).create(values)

    def _load_analytic_tags(self):
        settings = self.env['res.config.settings'].create({
            'group_analytic_accounting': True,
            'module_account_budget': True,
        })
        settings.execute()

        self.env["account.analytic.line"].with_company(self.demo_data_id.company_id).search([]).unlink()
        budget = self.env['budget.analytic'].with_company(self.demo_data_id.company_id).search([])
        budget.action_budget_draft()
        budget.unlink()


        plan_id = self.env["account.analytic.plan"].with_company(self.demo_data_id.company_id).search([("name", "ilike", "Internal")], limit=1)
        if not plan_id:
            plan_id = self.env["account.analytic.plan"].with_company(self.demo_data_id.company_id).create({
                "name": "Internal",
                "default_applicability": "optional",
            })

        file_path = os.path.join(self.demo_data_id.data_path, "analytic_accounts.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                self.env["account.analytic.account"].with_company(
                    self.demo_data_id.company_id
                ).create({
                    "name": row.get("name"),
                    "code": row.get("code"),
                    "plan_id": plan_id.id,
                })

        account_dict = {rec._column_name(): rec.account_ids.ids for rec in [
                    self.env["account.analytic.plan"]
                     .with_company(self.demo_data_id.company_id)
                     .search([("name", "ilike", "Internal")])
                ]}

        budget_lines = []
        for combination in product(*account_dict.values()):
            line = dict(zip(account_dict.keys(), combination))
            if line.get('x_plan2_id'):
                account_id = self.env["account.analytic.account"].with_company(self.demo_data_id.company_id).search(
                    [('id', '=', line['x_plan2_id'])], limit=1)
                if 'PO' in account_id.code:
                    random_value = random.randint(30, 80) * 1000
                    line['budget_amount'] = random_value

            budget_lines.append((0, 0, line))

        name = partial(format_date, self.env, date_format='yyyy')
        step = relativedelta(years=1)
        current_year = fields.Date.today().year
        date_from = fields.Date.from_string(f"{current_year}-01-01")
        date_to = fields.Date.from_string(f"{current_year}-12-31")
        budget_vals = []
        for date_from in date_utils.date_range(date_from, date_to, step):
            date_to = date_from + step - relativedelta(days=1)
            budget_vals.append({
                'name': _('Budget %s', name(date_from)),
                'date_from': date_from,
                'date_to': date_to,
                'budget_type': 'expense',
                'budget_line_ids': budget_lines,
            })
        budget_id = self.env['budget.analytic'].create(budget_vals)
        budget_id.action_budget_confirm()


    def _load_bills(self):
        _logger.info(
            "fiscal localisation applied:", self.demo_data_id.company_id.chart_template
        )
        if self.env.ref("account.group_account_basic") not in self.env.user.group_ids:
            self.env.user.group_ids += self.env.ref("account.group_account_basic")

        account_move_list = self.env["account.move"].search([("company_id","=",self.demo_data_id.company_id.id)])
        for account_move in account_move_list:
            if account_move.state == "posted":
                account_move.button_draft()
        account_move_list.unlink()
        self.env["account_followup.followup.line"].with_company(self.demo_data_id.company_id).search([]).unlink()

        def create_bills(file_name):
            with open(
                os.path.join(self.demo_data_id.data_path, file_name),
                mode="r",
                encoding="utf-8-sig",
            ) as csv_file:
                csv_reader = csv.DictReader(csv_file)
                n = 0
                for row in csv_reader:
                    row["partner_id"] = (
                        self.env["res.partner"].with_company(self.demo_data_id.company_id)
                        .search([("name", "ilike", row.get("partner_name"))], limit=1)
                        .id
                    )
                    row["invoice_date"] = row.get("date")
                    row["amount_untaxed"] = float(row.get("amount_untaxed").replace(",",""))
                    dict_line = {
                        "product_id": row.get("invoice_line_ids/product_name"),
                        "quantity": row.get("invoice_line_ids/quantity"),
                        "price_unit": float(row.get("invoice_line_ids/price_unit").replace(",","")),
                    }
                    del (
                        row["invoice_line_ids/product_name"],
                        row["invoice_line_ids/quantity"],
                        row["invoice_line_ids/price_unit"],
                        row["partner_name"],
                    )
                    dict_line["product_id"] = (
                        self.env["product.product"].with_company(self.demo_data_id.company_id)
                        .search([("name", "ilike", dict_line.get("product_id"))], limit=1)
                        .id
                    )
                    if self.demo_data_id.accountant:
                        if row["move_type"] == "in_invoice":
                            analytic_accounts = self.env["account.analytic.account"].with_company(
                                self.demo_data_id.company_id).search([("code", "ilike", "PO")])
                            if analytic_accounts:
                                random_account = random.choice(analytic_accounts)
                                analytic_dict = {str(random_account.id): 100.0}
                                dict_line["analytic_distribution"] = analytic_dict

                        if row["move_type"] == "out_invoice":
                            analytic_accounts = self.env["account.analytic.account"].with_company(
                                self.demo_data_id.company_id).search([("code", "ilike", "SO")])
                            if analytic_accounts:
                                random_account = random.choice(analytic_accounts)
                                analytic_dict = {str(random_account.id): 100.0}
                                dict_line["analytic_distribution"] = analytic_dict

                    vals = {
                        "partner_id": row.get("partner_id"),
                        "invoice_date": row.get("invoice_date"),
                        "amount_untaxed": row.get("amount_untaxed"),
                        "invoice_date_due": row.get("invoice_date_due"),
                        "move_type": row.get("move_type"),
                        "date": row.get("date")
                    }
                    move_id = (
                        self.env["account.move"]
                        .with_company(self.demo_data_id.company_id)
                        .create(vals)
                    )
                    dict_line["move_id"] = move_id.id
                    self.env["account.move.line"].with_company(
                        self.demo_data_id.company_id
                    ).create(dict_line)
                    move_id.action_post()
                    if n > 5 and move_id.move_type == "out_invoice":
                        self.env['account.move.send']._generate_and_send_invoices(move_id)

                    n += 1

        def create_transaction(is_in_invoice: bool, limit=8, threshold=6):
            n = 0
            for bill in (
                self.with_company(self.demo_data_id.company_id)
                .env["account.move"]
                .search(
                    [
                        (
                            "move_type",
                            "=",
                            "in_invoice" if is_in_invoice else "out_invoice",
                        ),
                        ("company_id", "=", self.demo_data_id.company_id.id),
                    ],
                    limit=limit,
                    order="invoice_date_due asc",
                )
            ):
                new_transaction = {
                    "date": bill.invoice_date,
                    "move_type": bill.move_type,
                    "amount": bill.amount_total * (-1 if is_in_invoice else 1),
                }
                if n < threshold:
                    new_transaction["payment_ref"] = bill.name
                    new_transaction["partner_id"] = bill.partner_id.id
                if n >= threshold:
                    new_transaction["payment_ref"] = bill.partner_id.name
                self.env["account.bank.statement.line"].with_company(
                    self.demo_data_id.company_id
                ).create(new_transaction)
                n += 1

        create_bills("customer_bills.csv")
        create_bills("supplier_bills.csv")
        create_transaction(False, limit=16, threshold=14)
        create_transaction(True)

        with open(os.path.join(self.demo_data_id.data_path, "followups.csv"), mode="r",
                  encoding="utf-8-sig", ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            n = 0
            for row in csv_reader:
                vals = {
                    "name": row.get("name"),
                    "delay": row.get("delay"),
                    "create_activity": row.get("create_activity"),
                    "mail_template_id": self.env["mail.template"].search(
                        [("name", "ilike", row.get("mail_template_id"))], limit=1).id,
                    "auto_execute": row.get("auto_execute") if n >= 2 else False,
                }

                if vals.get("create_activity"):
                    vals.update({"activity_type_id": self.env["mail.activity.type"].search(
                        [("name", "ilike", row.get("activity_type_id"))], limit=1).id,
                                 "activity_summary": row.get("activity_summary"),
                                 "activity_note": row.get("activity_note"),
                                 })

                self.env["account_followup.followup.line"].with_company(self.demo_data_id.company_id).create(vals)
                n += 1

        self.env["res.partner"].with_company(self.demo_data_id.company_id).search(
            [("followup_status", "=", "in_need_of_action")]).action_manually_process_automatic_followups()

    def _load_data_asset(self):
        with open(
            os.path.join(self.demo_data_id.data_path, "assets.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["account_depreciation_id"] = (self.env["account.account"].search(
                    [("id", "=", row.get("account_depreciation_id")),
                     ("company_ids", "parent_of", self.demo_data_id.company_id.id,), ], limit=1, ).id)

                row["account_depreciation_expense_id"] = (self.env["account.account"].search(
                    [("id", "=", row.get("account_depreciation_expense_id")),
                     ("company_ids", "parent_of", self.demo_data_id.company_id.id,), ], limit=1, ).id)

                vals = {
                    "method_period": "1",
                    "account_depreciation_expense_id": row.get("account_depreciation_expense_id"),
                    "account_depreciation_id": row.get("account_depreciation_id"),
                    "name": row.get("name"),
                    "method": row.get("method"),
                    "original_value": row.get("original_value"),
                    "acquisition_date": row.get("acquisition_date"),
                    "salvage_value": row.get("salvage_value"),
                    "method_number": row.get("method_number"),
                }
                self.env["account.asset"].with_company(
                    self.demo_data_id.company_id
                ).create(vals)
            assets = self.env['account.asset'].search([('company_id','=',self.demo_data_id.company_id.id)])
            assets.validate()

    def _load_data_pdf(self):
        n = 0
        pdfkit_options = {"encoding": "UTF-8"}

        for invoice in self.env["account.move"].search(
            [("move_type", "=", "in_invoice"), ("invoice_pdf_report_file", "=", False),
             ("company_id","=",self.demo_data_id.company_id.id)],
            limit=5,
        ):
            if invoice.invoice_pdf_report_file:
                continue
            path = os.path.join(
                self.demo_data_id.data_path, "invoice" + str(invoice.id) + ".pdf"
            )
            if os.path.isfile(path):
                with open(path, "rb") as pdf_binary:
                    invoice.invoice_pdf_report_file = base64.b64encode(
                        pdf_binary.read()
                    )
                continue

            dict_replace = {
                "__supplier_name__": invoice.partner_id.name,
                "__supplier_city__": invoice.partner_id.city,
                "__supplier_country__": invoice.partner_id.country_id.name,
                "__name__": self.demo_data_id.name,
                "__city__": self.demo_data_id.city,
                "__country__": self.demo_data_id.country_id.name,
                "__product_name__": invoice.line_ids[0].product_id.name,
                "__product_qty__": str(invoice.line_ids[0].quantity),
                "__product_price__": str(invoice.line_ids[0].price_unit),
                "__bill_date__": str(invoice.invoice_date),
                "__due_date__": str(invoice.invoice_date_due),
                "__tax_total__": str(invoice.amount_total),
            }
            new_prompt = self.prompt
            for key in dict_replace:
                if dict_replace[key]:
                    new_prompt = new_prompt.replace(key, dict_replace[key])
                else:
                    new_prompt = new_prompt.replace(key, "<UNKNOWN>")
            result = cap_api_call.mistral_api_call(
                MISTRAL_SMALL,
                new_prompt,
                self.demo_data_id.api_key_id.name,
                self.demo_data_id.lang,
            )
            html = result.choices[0].message.content
            regex_pattern = r"```html(.+?)```"
            match = re.findall(regex_pattern, html, flags=re.DOTALL)[0]

            pdfkit.from_string(match, path, options=pdfkit_options)
            with open(path, "rb") as pdf_binary:
                pdf_data = pdf_binary.read()
            pdf_data = base64.b64encode(pdf_data)
            invoice.invoice_pdf_report_file = pdf_data
            n += 1
        if self.env["account.move"].with_company(self.demo_data_id.company_id).search(
            [("move_type", "=", "in_invoice"), ("invoice_pdf_report_file", "=", False),
             ("company_id","=",self.demo_data_id.company_id.id)]
        ):
            self.copy({"state": "in_line", "nb_tries": 0})

    def _load_data_crm(self):
        with open(os.path.join(self.demo_data_id.data_path, "crm_tags.csv"), mode="r",
                  encoding="utf-8-sig", ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for tag_row in csv_reader:
                if not self.env["crm.tag"].search([("name","ilike",tag_row.get("name"))]):
                    self.env["crm.tag"].create({
                        "name": tag_row.get("name"),
                        "color": tag_row.get("color"),
                    })


        self.env["crm.lead"].search([("company_id","=",self.demo_data_id.company_id.id)]).unlink()

        with open(
            os.path.join(self.demo_data_id.data_path, "crm_leads.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            n = 0
            for row in csv_reader:
                user_name = row.get("user_id", "").strip()
                company_id = self.demo_data_id.company_id.id
                employee = self.env['hr.employee'].search(
                    [('name', '=', user_name), ('company_id', '=', company_id)], limit=1)
                user_exist = self.env['res.users'].search([('name', '=', user_name)], limit=1)

                if not employee:
                    continue

                user_partner_ids = employee._get_related_partners()
                user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                existing_emp_same_user = self.env['hr.employee'].search([
                    ('user_id', '=', user_exist.id),
                    ('company_id', '=', company_id)
                ], limit=1)

                if not user_exist:
                    self.env["res.users"].create({'company_id': self.demo_data_id.company_id.id,
                                                  'name': user_name,
                                                  'login': user_name,
                                                  'work_phone': employee.work_phone,
                                                  'work_email': employee.work_email,
                                                  'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                                  'employee_ids': [(4, employee.id)],
                                                  'partner_id': user_partner_id, })

                else:
                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id

                user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                  'name': user_name,
                                  'login': user_name if user_exist.id != 2 else user_exist.login,
                                  'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                  'work_phone': employee.work_phone,
                                  'work_email': employee.work_email})

                self.env['res.users'].search([]).write({'company_ids': [(6,0, (self.env["res.company"].search([]).ids))]})

                row["stage_id"] = self.env["crm.stage"].search([("name", "=", row.get("stage_name"))],
                                                               limit=1).id

                del row["stage_name"]
                row["partner_id"] = self.env["res.partner"].with_company(self.demo_data_id.company_id).search(
                        [("name", "ilike", row.get("partner_id"))], limit=1).id

                vals = {
                    "stage_id": row.get("stage_id"),
                    "partner_id": row.get("partner_id"),
                    "user_id": self.env['res.users'].search([('name','=',user_name)], limit=1).id,
                    "name": row.get("name"),
                    "tag_ids": [(6, 0, (self.env["crm.tag"].search([("name", "ilike", row.get("tag_ids"))]).ids))],
                    "expected_revenue": row.get("expected_revenue"),
                    "probability": row.get("probability"),
                    "email_from": row.get("email_from"),
                    "date_deadline": row.get("date_deadline"),
                    "priority": row.get("priority")
                }

                lead_id = self.env["crm.lead"].with_company(self.demo_data_id.company_id).create(vals)

                if n < 3:
                    pass
                elif n < 5:
                    lead_id.action_set_won_rainbowman()
                n += 1

        with open(
            os.path.join(self.demo_data_id.data_path, "crm_activities.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["activity_type_id"] = (
                    self.env["mail.activity.type"]
                    .search([("name", "=", row.get("activity_type_id"))])
                    .id
                )
                row["res_model"] = "crm.lead"
                row["res_model_id"] = self.env["ir.model"]._get("crm.lead").id
                row["res_id"] = (
                    self.env["crm.lead"]
                    .search([("name", "ilike", row.get("lead_name")),
                    ("company_id","=",self.demo_data_id.company_id.id)], limit=1)
                    .id
                )
                row["user_id"] = self.env["crm.lead"].search([("name", "ilike", row.get("lead_name")),
                    ("company_id","=",self.demo_data_id.company_id.id)], limit=1).user_id.id

                del row["lead_name"]
                self.env["mail.activity"].with_company(
                    self.demo_data_id.company_id
                ).create(row)

    def _load_data_sales(self):
        pricelist_id = self.env["product.pricelist"].search([("name", "ilike", f"Default {self.demo_data_id.name}"),
                                                             ("company_id", "=", self.demo_data_id.company_id.id)],limit=1)
        if not pricelist_id:
            pricelist_id = self.env["product.pricelist"].create({
                "name": f"Default {self.demo_data_id.name}",
                "company_id": self.demo_data_id.company_id.id,
                "currency_id": self.demo_data_id.company_id.currency_id.id,
            })

        is_stock: bool = bool(
            self.env["ir.module.module"].search([("name", "=", "stock")], limit=1).state
            in ["installed", "to_remove", "to_upgrade"]
            or self.demo_data_id.stock
        )

        if is_stock:
            warehouse = self.env["stock.warehouse"].search(
                [("name", "=", self.demo_data_id.name)], limit=1
            )
            if not warehouse:
                    (self.env["stock.warehouse"].with_company(self.demo_data_id.company_id)
                     .create({"name": self.demo_data_id.name, "code": "STOCK"}))

        if is_stock:
            config_parameter = (
                self.env["ir.config_parameter"]
                .with_company(self.demo_data_id.company_id)
                .sudo()
            )  # with company might be useless
            config_parameter.set_param("stock.sms_confirmation", "False")
        with open(
            os.path.join(self.demo_data_id.data_path, "sales.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            n = 0
            for row in csv_reader:
                row["amount_untaxed"] = float(row.get("amount_untaxed").replace(",",""))

                row["partner_id"] = (
                    self.env["res.partner"].with_company(self.demo_data_id.company_id)
                    .search([("name", "ilike", row.get("partner_id"))], limit=1)
                    .id
                )

                dict_line = {
                    "name": row.get("order_line/product_id"),
                    "product_id": row.get("order_line/product_id"),
                    "product_uom_qty": row.get("order_line/quantity"),
                    "price_unit": float(row.get("price_unit").replace(",","")),
                }
                del (
                    row["order_line/product_id"],
                    row["order_line/quantity"],
                    row["price_unit"],
                )
                product = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                    [("name", "=", dict_line.get("product_id"))], limit=1)

                dict_line["product_id"] = product.id

                vals = {
                    "name": row.get("name"),
                    "partner_id": row.get("partner_id"),
                    "date_order": row.get("date_order"),
                    "amount_untaxed": row.get("amount_untaxed"),
                    "pricelist_id": pricelist_id.id,
                    "company_id": self.demo_data_id.company_id.id,
                }

                order_id = (
                    self.env["sale.order"]
                    .with_company(self.demo_data_id.company_id)
                    .create(vals)
                )

                if n < 3:
                    pass
                elif n < 5:
                    order_id.action_quotation_send()
                else:
                    if self.demo_data_id.accountant:
                        analytic_accounts = self.env["account.analytic.account"].with_company(
                            self.demo_data_id.company_id).search([("code", "ilike", "SO")])
                        if analytic_accounts:
                            random_account = random.choice(analytic_accounts)
                            analytic_dict = {str(random_account.id): 100.0}
                            dict_line["analytic_distribution"] = analytic_dict

                    order_id.action_confirm()

                dict_line["order_id"] = order_id.id
                self.env["sale.order.line"].with_company(
                    self.demo_data_id.company_id
                ).create(dict_line)
                if n >= 10 and is_stock and product.type == "consu":
                    try:
                        picking = (
                            self.env["stock.picking"]
                            .with_company(self.demo_data_id.company_id)
                            .search([("origin", "=", order_id.name)], limit=1)
                        )
                        picking.button_validate()
                        if picking.state != "done":
                            for move in picking.move_ids:
                                move._action_done()
                    except Exception as e:
                        _logger.error("Delivery error:\n", e)
                if n >= 15:
                    has_non_service = any(
                        line.product_id
                        and line.product_id.type != 'service'
                        for line in order_id.order_line
                    )

                    if has_non_service and is_stock:
                        try:
                            invoices = order_id._create_invoices()
                            if self.demo_data_id.accountant:
                                invoices.invoice_line_ids.analytic_distribution = dict_line["analytic_distribution"]
                            invoices.action_post()
                        except Exception as e:
                            _logger.error("Invoice Exception:\n", e)

                    if has_non_service and not is_stock:
                        order_id.order_line[0].qty_delivered = order_id.order_line[0].product_uom_qty
                        try:
                            invoices = order_id._create_invoices()
                            invoices.action_post()
                        except Exception as e:
                            _logger.error("Invoice Exception:\n", e)
                n += 1


        settings = self.env['res.config.settings'].create({
            'group_sale_order_template': True,
        })
        settings.execute()
        self.env["sale.order.template"].with_company(self.demo_data_id.company_id).search([("company_id","=",False)]).unlink()
        partners = self.env["res.partner"].with_company(self.demo_data_id.company_id).search(
            [("category_id.name", "ilike", "customer")])

        with open(os.path.join(self.demo_data_id.data_path, "quotation_templates.csv"), mode="r",
                  encoding="utf-8-sig", ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                products = row.get("order_line/product_id").split(";")
                quantities = row.get("order_line/quantity").split(";")
                lines = []
                for product_id, quantity in zip(products, quantities):
                    prod = self.env["product.product"].search([("name", "ilike", product_id)], limit=1)
                    lines.append((0, 0, {
                        "product_id": prod.id,
                        "product_uom_qty": quantity,
                        "name": prod.name,
                    }))

                lines.append((0,0,{
                    "name": row.get("note"),
                    "display_type": "line_note",
                }))


                vals = {
                    "name": row.get("name"),
                    "mail_template_id": self.env["mail.template"].search(
                        [("name", "ilike", "Sales: Order Confirmation")], limit=1).id,
                    "sale_order_template_line_ids": lines,
                    "note": row.get("terms_and_conditions"),
                }
                created_template = self.env["sale.order.template"].with_company(self.demo_data_id.company_id).create(vals)

                sale_order_id = self.env["sale.order"].create({
                    "partner_id": random.choice(partners).id if partners else False,
                    "sale_order_template_id": created_template.id,
                    "company_id": self.demo_data_id.company_id.id,
                })
                sale_order_template = created_template.with_context(lang=sale_order_id.partner_id.lang)

                order_lines_data = [fields.Command.clear()]
                order_lines_data += [
                    fields.Command.create(line._prepare_order_line_values())
                    for line in sale_order_template.sale_order_template_line_ids
                ]

                if len(order_lines_data) >= 2:
                    order_lines_data[1][2]["sequence"] = -99

                sale_order_id.write({
                    "order_line": order_lines_data
                })



    def _load_header_pdf(self):
        self.env["quotation.document"].with_company(self.demo_data_id.company_id).search([( "document_type","=", "header")]).unlink()
        pdfkit_options = {"encoding": "UTF-8"}

        for i in range(1, 4):
            path = os.path.join(self.demo_data_id.data_path, f"quotation_header_{i}.pdf")
            if os.path.isfile(path):
                Path(path).unlink()

            dict_replace = {
                "__company_name__": self.demo_data_id.name,
                "__city__": self.demo_data_id.city,
                "__country__": self.demo_data_id.country_id.name,
                "__index__": str(i),
            }

            new_prompt = self.prompt
            for key in dict_replace:
                if dict_replace[key]:
                    new_prompt = new_prompt.replace(key, dict_replace[key])
                else:
                    new_prompt = new_prompt.replace(key, "<UNKNOWN>")

            result = cap_api_call.mistral_api_call(
                MISTRAL_SMALL,
                new_prompt,
                self.demo_data_id.api_key_id.name,
                self.demo_data_id.lang,
            )

            html = result.choices[0].message.content
            regex_pattern = r"```html(.+?)```"
            match = re.findall(regex_pattern, html, flags=re.DOTALL)[0]

            pdfkit.from_string(match, path, options=pdfkit_options)

            with open(path, "rb") as pdf_binary:
                pdf_data = base64.b64encode(pdf_binary.read())

            quot_templates = self.env["sale.order.template"].with_company(self.demo_data_id.company_id).search([("company_id","=",self.demo_data_id.company_id.id)])
            self.env["quotation.document"].with_company(self.demo_data_id.company_id).create({
                "name": f"Quotation Header {i}",
                "document_type": "header",
                "datas": pdf_data,
                "mimetype": "application/pdf",
                "quotation_template_ids": [(6, 0, quot_templates.ids)],
            })

    def _load_footer_pdf(self):
        self.env["quotation.document"].with_company(self.demo_data_id.company_id).search([( "document_type","=", "footer")]).unlink()
        pdfkit_options = {"encoding": "UTF-8"}

        for i in range(1, 4):
            path = os.path.join(self.demo_data_id.data_path, f"quotation_footer_{i}.pdf")
            if os.path.isfile(path):
                Path(path).unlink()

            dict_replace = {
                "__company_name__": self.demo_data_id.name,
                "__city__": self.demo_data_id.city,
                "__country__": self.demo_data_id.country_id.name,
                "__index__": str(i),
            }

            new_prompt = self.prompt
            for key in dict_replace:
                if dict_replace[key]:
                    new_prompt = new_prompt.replace(key, dict_replace[key])
                else:
                    new_prompt = new_prompt.replace(key, "<UNKNOWN>")

            result = cap_api_call.mistral_api_call(
                MISTRAL_SMALL,
                new_prompt,
                self.demo_data_id.api_key_id.name,
                self.demo_data_id.lang,
            )

            html = result.choices[0].message.content
            regex_pattern = r"```html(.+?)```"
            match = re.findall(regex_pattern, html, flags=re.DOTALL)[0]

            pdfkit.from_string(match, path, options=pdfkit_options)

            with open(path, "rb") as pdf_binary:
                pdf_data = base64.b64encode(pdf_binary.read())

            quot_templates = self.env["sale.order.template"].with_company(self.demo_data_id.company_id).search(
                [("company_id", "=", self.demo_data_id.company_id.id)])
            self.env["quotation.document"].with_company(self.demo_data_id.company_id).create({
                "name": f"Quotation Footer {i}",
                "document_type":"footer",
                "datas": pdf_data,
                "mimetype": "application/pdf",
                "quotation_template_ids": [(6, 0, quot_templates.ids)],
            })

        sale_orders = self.env["sale.order"].with_company(self.demo_data_id.company_id).search(
            [("sale_order_template_id", "!=", False), ("company_id", "=", self.demo_data_id.company_id.id)])

        documents = self.env["quotation.document"].with_company(self.demo_data_id.company_id).search(
            [("company_id", "=", self.demo_data_id.company_id.id)])

        headers = documents.filtered(lambda d: d.document_type == "header")
        footers = documents.filtered(lambda d: d.document_type == "footer")

        template = self.env.ref('sale.email_template_edi_sale', raise_if_not_found=False)
        n = 0
        for order in sale_orders:
            random_header = random.choice(headers) if headers else False
            random_footer = random.choice(footers) if footers else False

            order.quotation_document_ids = [(6, 0, (random_header | random_footer).ids)]
            if n < 2:
                wizard = order.env['mail.compose.message'].with_context(validate_analytic= True,
                    check_document_layout= True,
                    active_model = 'sale.order',
                    active_id = order.id,
                    default_model = 'sale.order',
                    default_composition_mode = 'comment',
                    default_email_layout_xmlid = 'mail.mail_notification_layout_with_responsible_signature',
                    email_notification_allow_footer = True,
                    hide_mail_template_management_options = True,
                    proforma = False,
                    force_email = True,
                    default_template_id = template.id,
                    mark_so_as_sent = True
                ).create({
                    "message_type": 'comment',
                })

                wizard._action_send_mail(auto_commit=True)

            n += 1


    def _load_data_purchase(self):
        is_stock: bool = bool(
            self.env["ir.module.module"].search([("name", "=", "stock")], limit=1).state
            in ["installed", "to_remove", "to_upgrade"]
            or self.demo_data_id.stock
        )

        if is_stock:
            warehouse = self.env["stock.warehouse"].search(
                [("name", "=", self.demo_data_id.name)], limit=1
            )
            if not warehouse:
                    (self.env["stock.warehouse"].with_company(self.demo_data_id.company_id)
                     .create({"name": self.demo_data_id.name, "code": "STOCK"}))

        with open(
            os.path.join(self.demo_data_id.data_path, "purchase.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            n = 0
            if is_stock:
                picking_type_id = (
                    self.env["stock.picking.type"]
                    .with_company(self.demo_data_id.company_id)
                    .search([("name", "=", "Stock"),("company_id","=",self.demo_data_id.company_id.id)], limit=1)
                    .id)
                if not picking_type_id:
                    self.env["stock.picking.type"].with_company(
                        self.demo_data_id.company_id).create({"name": "Stock",
                                                              "sequence_code": "STOCK",
                                                              "company_id": self.demo_data_id.company_id.id,})

                picking_type_id = (
                    self.env["stock.picking.type"]
                    .with_company(self.demo_data_id.company_id)
                    .search([("name", "=", "Stock"),("company_id","=",self.demo_data_id.company_id.id)], limit=1)
                    .id)

            for row in csv_reader:
                partner_id = self.env["res.partner"].with_company(self.demo_data_id.company_id
                                                                  ).search([("name", "ilike", row.get("partner_id"))],
                                                            limit=1).id

                if not partner_id:
                    continue

                row["partner_id"] = partner_id
                row["amount_untaxed"] = float(row.get("amount_untaxed").replace(",",""))

                dict_line = {
                    "name": row.get("order_line/name"),
                    "product_id": row.get("order_line/product_id"),
                    "product_qty": row.get("order_line/quantity"),
                    "price_unit": float(row.get("order_line/price_unit").replace(",","")),
                }
                del (
                    row["order_line/name"],
                    row["order_line/product_id"],
                    row["order_line/quantity"],
                    row["order_line/price_unit"],
                )
                product = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                    ["|", ("name", "ilike", dict_line.get("product_id")),
                     ("default_code", "ilike", dict_line.get("product_id"))], limit=1)

                dict_line["product_id"] = product.id

                vals = {
                    "partner_id": row.get("partner_id"),
                    "date_order": row.get("date_order"),
                    "amount_untaxed": row.get("amount_untaxed"),
                    "date_planned": row.get("date_planned"),
                    "name": row.get("name"),
                }
                if is_stock:
                    vals["picking_type_id"] = picking_type_id


                order_id = (
                    self.env["purchase.order"]
                    .with_company(self.demo_data_id.company_id)
                    .create(vals)
                )

                if n < 3:
                    pass
                elif n < 5:
                    order_id.action_rfq_send()
                else:
                    if self.demo_data_id.accountant:
                        analytic_accounts = self.env["account.analytic.account"].with_company(
                            self.demo_data_id.company_id).search([("code", "ilike", "PO")])
                        if analytic_accounts:
                            random_account = random.choice(analytic_accounts)
                            analytic_dict = {str(random_account.id): 100.0}
                            dict_line["analytic_distribution"] = analytic_dict

                    order_id.button_confirm()


                dict_line["order_id"] = order_id.id
                self.env["purchase.order.line"].with_company(
                    self.demo_data_id.company_id
                ).create(dict_line)
                if n > 10 and product.type == "consu":
                    order_id.action_create_invoice()
                    bill = (
                        self.env["account.move"]
                        .with_company(self.demo_data_id.company_id)
                        .search([("state", "=", "draft")], limit=1)
                    )
                    bill.auto_post = 'no'
                    bill.invoice_date = bill.date
                    bill.action_post()
                n += 1

    def _load_data_stock(self):
        self.env["stock.warehouse.orderpoint"].with_company(self.demo_data_id.company_id).search([("company_id", "=", self.demo_data_id.company_id.id)]).unlink()
        self.env["stock.quant"].with_company(self.demo_data_id.company_id).search([("company_id", "=", self.demo_data_id.company_id.id)]).unlink()

        settings = self.env['res.config.settings'].create({
            'group_stock_multi_locations': True,
            'group_stock_adv_location': True,
            'replenish_on_order': True,
        })
        settings.execute()
        warehouse = self.env["stock.warehouse"].search(
            [("name", "=", self.demo_data_id.name),
             ("company_id","=",self.demo_data_id.company_id.id)], limit=1
        )
        if not warehouse:
            warehouse = (
                self.env["stock.warehouse"]
                .with_company(self.demo_data_id.company_id)
                .create({"name": self.demo_data_id.name, "code": "STOCK"})
            )

        self.env["stock.picking.type"].with_company(
            self.demo_data_id.company_id).create({"name": "Stock",
                                                  "sequence_code": "STOCK", })

        with open(
            os.path.join(self.demo_data_id.data_path, "stock.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                product = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                    [("name", "ilike", row.get("product_id"))], limit=1
                )
                row["product_id"] = product.id
                row["location_id"] = warehouse.lot_stock_id.id
                row["quantity"] = row.get("inventory_quantity")
                if not product.is_storable:
                    continue

                vals = {
                    "product_id": row.get("product_id"),
                    "location_id": row.get("location_id"),
                    "quantity": row.get("quantity"),
                    "inventory_quantity": row.get("inventory_quantity"),
                    "inventory_date": row.get("inventory_date"),
                }
                self.env["stock.quant"].with_company(
                    self.demo_data_id.company_id
                ).create(vals)
        config_parameter = self.env["ir.config_parameter"].sudo()
        config_parameter.set_param("stock.sms_confirmation", "False")

        with open(os.path.join(self.demo_data_id.data_path, "replenishments.csv"), mode="r",
                  encoding="utf-8-sig", ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            n = 0
            for reorder_row in csv_reader:
                product_id = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                    [("name", "ilike", reorder_row.get("product_id"))],limit=1)
                if product_id.type != "service":
                    vals = {
                        "product_id": product_id.id,
                        "product_min_qty": reorder_row.get("min_qty"),
                        "product_max_qty": reorder_row.get("max_qty"),
                        "qty_to_order": reorder_row.get("qty_to_order"),
                        "trigger": "auto" if n > 5 else "manual",
                        "location_id": warehouse.lot_stock_id.id,
                    }
                    rule_id = self.env["stock.warehouse.orderpoint"].with_company(self.demo_data_id.company_id).create(vals)
                    if n < 6 and rule_id.trigger == "manual":
                        rule_id.write({
                            "product_min_qty": rule_id.qty_on_hand + 5,
                            "product_max_qty": rule_id.qty_on_hand + 20,
                        })
                n += 1

        self.env["stock.warehouse.orderpoint"].with_company(self.demo_data_id.company_id).search([("trigger", "=", "manual")]).action_remove_manual_qty_to_order()

        if self.env["ir.module.module"].search([("name", "=", "purchase")], limit=1).state in ["installed",
                                                                                               "to_upgrade",
                                                                                               "to_remove"]:
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

    def _load_data_hr(self):
        with open(
            os.path.join(self.demo_data_id.data_path, "departments.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                self.env["hr.department"].with_company(
                    self.demo_data_id.company_id
                ).create({
                    "name": row.get("name"),
                })
        with open(
            os.path.join(self.demo_data_id.data_path, "employees.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)

            rows = [r for r in csv_reader if any(r.values())]

            if rows:
                self.env.user.name = rows[0]["name"]

            for row in rows:
                row["parent_id"] = (
                    self.env["hr.employee"].search([("name", "=", row.get("parent_id")),
                             ("company_id","=",self.demo_data_id.company_id.id)], limit=1).id)

                row["department_id"] = (
                    self.env["hr.department"].search([("name", "=", row.get("department_id")),
                             ("company_id","=",self.demo_data_id.company_id.id)], limit=1).id)

                vals = {
                    "parent_id": row.get("parent_id"),
                    "department_id": row.get("department_id"),
                    "name": row.get("name"),
                    "job_title": row.get("job_title"),
                    "work_email": row.get("work_email"),
                    "work_phone": row.get("work_phone"),
                    "hourly_cost": row.get("hourly_cost"),
                    "sex": row.get("sex"),
                }

                self.env["hr.employee"].with_company(
                    self.demo_data_id.company_id
                ).create(vals)

            current_user = self.env.user
            current_user_employee_id = self.env["hr.employee"].search(
                [("name", "ilike", current_user.name),
                 ("company_id", "=", self.demo_data_id.company_id.id)])
            current_user_employee_id.user_id = current_user.id

    def _load_product_pictures(self):
        image_agent = ImageAgent(self.demo_data_id.api_key_id.name)
        image_agent.get_agent()
        product_images = []
        id_list = []

        products = (self.env["product.template"].with_company(self.demo_data_id.company_id).
                    search([],limit=(self.demo_data_id.percent_product_pictures *
            len(self.env["product.template"].with_company(self.demo_data_id.company_id).search([]))),
        ))

        for product in products:
            path = os.path.join(
                self.demo_data_id.data_path,
                (product.name.lower().replace(" ", "_") + ".jpg").strip(),
            )

            if os.path.isfile(path):
                with open(path, "rb") as image:
                    product.image_1920 = base64.b64encode(image.read())
                product_images.append(path)
                id_list.append(product.id)
                continue

            new_prompt = (
                self.prompt.replace("__name__", self.demo_data_id.name)
                .replace("__product__", product.name)
            )

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
            cropped = os.path.join(
                self.demo_data_id.data_path,
                ("cropped_" + employee.name.lower().replace(" ", "_") + ".jpg").strip(),
            )
            im.save(cropped)
            return cropped

        id_list = []
        image_agent = ImageAgent(self.demo_data_id.api_key_id.name)
        image_agent.get_agent()
        male_employee_picture = []
        female_employee_picture = []
        id_list = []
        for employee in self.env["hr.employee"].search(
            [("company_id", "=", self.demo_data_id.company_id.id)],
            limit=(
                self.demo_data_id.percent_employees_pictures
                * self.demo_data_id.nb_employees
            ),
        ):
            path = os.path.join(
                self.demo_data_id.data_path,
                (
                    employee.sex[0]
                    + "_"
                    + employee.name.lower().replace(" ", "_")
                    + ".jpg"
                ).strip(),
            )
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
        with open(
            os.path.join(self.demo_data_id.data_path, "component_products.csv"),
            "r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["route_ids"] = self.env["stock.route"].search(
                    [("name", "=", row.get("route_ids")),
                     ("company_id","=",self.demo_data_id.company_id.id)], limit=1
                )
                if self.demo_data_id.bool_company_restricted:
                    row["company_id"] = self.demo_data_id.company_id.id

                vals = {
                    "name": row.get("name"),
                    "list_price": row.get("list_price"),
                    "default_code": row.get("default_code"),
                    "purchase_ok": row.get("purchase_ok"),
                    "route_ids": row.get("route_ids").ids,
                    "company_id": row.get("company_id") if row.get("company_id") else False,
                }
                self.env["product.template"].with_company(
                    self.demo_data_id.company_id
                ).create(vals)
        with open(
            os.path.join(self.demo_data_id.data_path, "bom.csv"),
            "r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["product_tmpl_id"] = (
                    self.env["product.template"].with_company(self.demo_data_id.company_id).search(
                        [("name", "ilike", row.get("product_tmpl_id"))], limit=1).id)

                vals = {
                    "product_tmpl_id": row.get("product_tmpl_id"),
                    "product_qty": row.get("product_qty"),
                    "code": row.get("code"),
                }

                self.env["mrp.bom"].with_company(self.demo_data_id.company_id).create(vals)

        settings = self.env['res.config.settings'].create({
            'group_uom': True,
            'group_mrp_routings':True,
        })
        settings.execute()

    def _load_mrp_components(self):
        with open(
            os.path.join(self.demo_data_id.data_path, "bom_components.csv"),
            "r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                bom = self.env["mrp.bom"].search([("code", "=", row.get("code")),
                                                  ("company_id", "=", self.demo_data_id.company_id.id)
                                                  ], limit=1)

                if not bom:
                    continue

                product = self.env["product.template"].with_company(self.demo_data_id.company_id).search([
                    ("name", "ilike", row.get("product_id"))], limit=1)

                if not product:
                    continue

                if product.id == bom.product_tmpl_id.id:
                    continue

                row.pop("code", None)
                row["bom_id"] = bom.id
                row["product_id"] = product.id

                vals = {
                    "product_id": row.get("product_id"),
                    "product_qty": row.get("product_qty"),
                    "bom_id": row.get("bom_id"),
                }

                self.env["mrp.bom.line"].with_company(self.demo_data_id.company_id).create(vals)


    def _load_work_centers(self):
        self.env["ir.config_parameter"].set_param("mrp.group_mrp_routings", True)
        self.env.user.group_ids += self.env.ref("mrp.group_mrp_routings")
        with open(
            os.path.join(self.demo_data_id.data_path, "work_centers.csv"),
            "r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                vals = {
                    "name": row.get("name"),
                    "code": row.get("code"),
                    "time_efficiency": row.get("time_efficiency"),
                    "costs_hour": row.get("costs_hour"),
                }

                self.env["mrp.workcenter"].with_company(self.demo_data_id.company_id).create(vals)

    def _load_work_orders(self):
        with open(
            os.path.join(self.demo_data_id.data_path, "work_orders.csv"),
            "r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["workcenter_id"] = self.env["mrp.workcenter"].search(
                    [("code", "=", row.get("workcenter_id")), ("company_id", "=", self.demo_data_id.company_id.id)],
                    limit=1).id

                row["bom_id"] = self.env["mrp.bom"].search(
                    [("code", "=", row.get("bom_id")), ("company_id", "=", self.demo_data_id.company_id.id)],
                    limit=1).id

                vals = {
                    "workcenter_id": row.get("workcenter_id"),
                    "bom_id": row.get("bom_id"),
                    "name": row.get("name"),
                    "employee_ratio": row.get("employee_ratio"),
                    "time_cycle_manual": row.get("time_cycle_manual"),
                }

                self.env["mrp.routing.workcenter"].with_company(self.demo_data_id.company_id).create(vals)

    def _load_manufacturing_orders(self):
        with (open(
            os.path.join(self.demo_data_id.data_path, "manufacturing_orders.csv"),
            "r",
            encoding="utf-8-sig",
        ) as csv_file):
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                state = row.get("state")
                row["product_id"] = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                    [("name", "ilike", row.get("product_id"))], limit=1).id

                vals = {
                    "product_id": row.get("product_id"),
                    "product_qty": row.get("product_qty"),
                    "date_start": row.get("date_start"),
                }

                work_order = self.env["mrp.production"].with_company(self.demo_data_id.company_id).create(vals)

                work_order.action_confirm()
                if state == "in_progress":
                    work_order.action_start()
                if state == "done":
                    try:
                        work_order.button_mark_done()
                    except Exception:
                        pass

            current_user_emp = self.env["hr.employee"].search(
                [("user_id", "=", self.env.user.id),
                 ("company_id","=",self.demo_data_id.company_id.id)], limit=1
            )

            if not current_user_emp:
                self.env['hr.employee'].with_company(self.demo_data_id.company_id).create({
                    'name': self.env.user.name,
                    'user_id': self.env.user.id, })

        self._create_unbuild_scrap_ecos()


    def _load_bom_instructions(self):
        file_path = os.path.join(self.demo_data_id.data_path, "operation_instructions.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)

            picking_type = self.env["stock.picking.type"].search(
                [("code", "=", "mrp_operation"),
                 ("company_id","=",self.demo_data_id.company_id.id)], limit=1
            )

            test_type = self.env["quality.point.test_type"].search(
                [("technical_name", "=", "instructions")], limit=1
            )

            product_name_change = False
            for row in csv_reader:

                product = self.env["product.template"].with_company(self.demo_data_id.company_id).search(
                    [("name", "ilike", row.get("product_ids", "").strip())], limit=1
                )

                product_name_change = product
                if not product:
                    continue

                bom = self.env["mrp.bom"].search(
                    [("product_tmpl_id", "=", product.id),
                     ("company_id","=",self.demo_data_id.company_id.id)], limit=1
                )
                if not bom:
                    continue

                operation = self.env["mrp.routing.workcenter"].search(
                    [("bom_id", "=", bom.id), ("name", "ilike", row.get("name", "").strip()),
                     ("company_id","=",self.demo_data_id.company_id.id)],
                    limit=1,)

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

            if '[0USE-THIS-ONE]' not in product_name_change.name:
                product_name_change.write({'name': product_name_change.name + '[0USE-THIS-ONE]'})
                mrp_id = self.env['mrp.production'].search([('product_id','=',product_name_change.id),
                                                            ("company_id","=",self.demo_data_id.company_id.id)])
                if mrp_id:
                    mrp_id.action_confirm()
                    mrp_id.button_mark_done()
                if not mrp_id:
                    production_id = self.env["mrp.production"].with_company(self.demo_data_id.company_id).create({
                        'product_id': product_name_change.id,
                        'product_qty': 10,
                        'date_start': fields.Datetime.now(),
                    })
                    production_id.action_confirm()
                    production_id.button_mark_done()


    def _create_unbuild_scrap_ecos(self):
        mrp_production = self.env['mrp.production'].search([('state', '=', 'done'),
                                                            ("company_id","=",self.demo_data_id.company_id.id)], limit=2)
        for unbuild in mrp_production:
            unbuild_order = self.env['mrp.unbuild'].create({
                'product_id': unbuild.product_id.id,
                'product_qty': unbuild.product_qty,
                'mo_id': unbuild.id,
                'company_id': unbuild.company_id.id,
            })
            unbuild_order.action_validate()

        mrp_production_scrap = self.env['mrp.production'].search([('state', '=', 'confirmed'),
                                                                  ("company_id","=",self.demo_data_id.company_id.id)], limit=2)
        for scrap in mrp_production_scrap:
            scrap_order = self.env['stock.scrap'].create({
                'product_id': scrap.product_id.id,
                'scrap_qty': scrap.product_qty,
                'origin': scrap.name,
                'company_id': scrap.company_id.id,
            })
            scrap_order.action_validate()

        product_tmpl = self.env["product.template"].with_company(self.demo_data_id.company_id).create({
            'name': 'Demo Product ECO',
            'type': 'consu',
            'company_id': self.demo_data_id.company_id.id,
        })

        product_variant = product_tmpl.product_variant_id

        component_1 = self.env["product.product"].with_company(self.demo_data_id.company_id).create({
            'name': 'Component 1',
            'type': 'consu',
            'company_id': self.demo_data_id.company_id.id,
        })

        workcenter = self.env['mrp.workcenter'].search(
            [("company_id", "=", self.demo_data_id.company_id.id)],
            limit=1
        )

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
        self.env["product.combo"].search([("company_id", "=", self.demo_data_id.company_id.id)]).unlink()
        self.env["product.template"].with_company(self.demo_data_id.company_id).search(
            [("available_in_pos", "=", True), ("company_id", "=", self.demo_data_id.company_id.id)]).unlink()
        file_path = os.path.join(self.demo_data_id.data_path, "pos_products.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                category_id = row.get("category_id")
                if category_id:
                    category = self.env["pos.category"].with_company(
                        self.demo_data_id.company_id).search(
                        [("name", "ilike", category_id)], limit=1)
                    if not category:
                        category = self.env["pos.category"].with_company(
                            self.demo_data_id.company_id).create({"name": category_id})

                vals = {
                    "name": row.get("name"),
                    "list_price": row.get("list_price"),
                    "default_code": row.get("default_code"),
                    "type": "consu",
                    "pos_categ_ids": [(6, 0, [category.id])] if category_id else [],
                    "available_in_pos": True,
                    "company_id": self.demo_data_id.company_id.id,
                }

                self.env["product.template"].with_company(self.demo_data_id.company_id).create(vals)

        file_path = os.path.join(self.demo_data_id.data_path, "pos_combo_items.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                product_ids = []
                products_raw = row.get("products", "")
                for product_id in products_raw.split(";"):
                    product_id = product_id.strip()
                    if not product_id:
                        continue

                    product_id = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                        [("name", "ilike", product_id)], limit=1)

                    if not product_id:
                        continue
                    product_ids.append(product_id)


                vals = {
                    "name": row.get("name"),
                    "company_id": self.demo_data_id.company_id.id,
                    "combo_item_ids": [(0, 0, {"product_id": pid.id})for pid in product_ids],
                }
                self.env["product.combo"].with_company(self.demo_data_id.company_id).create(vals)

        combo_ids = self.env["product.combo"].search([("company_id", "=", self.demo_data_id.company_id.id)],
                                                            limit=1)
        self.env["product.template"].with_company(self.demo_data_id.company_id).create({
            "name": "Combo Product 1",
            "type": "combo",
            "available_in_pos": True,
            "combo_ids": [(6, 0,combo_ids.ids)],
            "list_price": sum(combo_ids.combo_item_ids.mapped('product_id.list_price')),
            "company_id": self.demo_data_id.company_id.id,
        })

    def _load_pos_orders(self):
        self.env["pos.config"].with_company(self.demo_data_id.company_id).sudo().load_onboarding_retail_scenario()
        file_path = os.path.join(self.demo_data_id.data_path, "pos_session.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                pos_config = self.env["pos.config"].search([("company_id","=",self.demo_data_id.company_id.id)], limit=1)

                vals = {
                    "name": row.get("name"),
                    "start_at": row.get("start_at"),
                    "stop_at": row.get("stop_at"),
                    "config_id": pos_config.id,
                }

                pos_session = self.env["pos.session"].with_company(self.demo_data_id.company_id).create(vals)
                pos_session.action_pos_session_close()

        file_path = os.path.join(self.demo_data_id.data_path, "pos_orders.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            pos_cash_payment_method_id = self.env["pos.payment.method"].search(
                [("company_id", "=", self.demo_data_id.company_id.id), ("name", "=", "Cash")], limit=1).id

            if not pos_cash_payment_method_id:
                pos_cash_payment_method_id = self.env["pos.payment.method"].create({"name": "Cash",
                                                       "company_id": self.demo_data_id.company_id.id,}).id

            for order_row in csv_reader:
                pos_session = self.env["pos.session"].search(
                    [("name", "ilike", order_row.get("session_id"))], limit=1)

                if not pos_session:
                    continue

                product_id = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                    [("name", "ilike", order_row.get("product_id"))], limit=1)

                lines_vals = {
                    "product_id": product_id.id,
                    "qty": 1,
                    "price_unit": product_id.list_price,
                    "price_subtotal": product_id.list_price,
                    "price_subtotal_incl": product_id.list_price,
                }

                vals = {
                    "name": order_row.get("name"),
                    "date_order": order_row.get("date_order"),
                    "session_id": pos_session.id,
                    "lines": [(0,0,lines_vals)],
                    "amount_tax": 0.0,
                    "amount_paid": product_id.list_price,
                    "amount_total": product_id.list_price,
                    "amount_return": 0.0,
                    "partner_id": self.env["res.partner"].with_company(self.demo_data_id.company_id).search(
                        [("name", "ilike", order_row.get("partner_id"))], limit=1).id,
                }

                pos_order = self.env["pos.order"].with_company(self.demo_data_id.company_id).create(vals)

                pos_order.add_payment({
                    'amount': product_id.list_price,
                    'payment_method_id': pos_cash_payment_method_id,
                    'name': 'Cash',
                    'pos_order_id': pos_order.id,
                })

                pos_order.action_pos_order_invoice()

        product_id = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                    [("name", "ilike", "Combo Product 1")], limit=1)
        pos_config = self.env["pos.config"].search([("company_id", "=", self.demo_data_id.company_id.id)], limit=1)

        self.env["pos.order"].with_company(self.demo_data_id.company_id).search([]).create({
            "name": "POS-COMBO-1",
            "date_order": fields.Datetime.now(),
            "session_id": self.env["pos.session"].create({"name": "Morning Session",
                                                          "start_at": fields.Datetime.now(),
                                                          "stop_at": fields.Datetime.now(),
                                                          "config_id": pos_config.id,}).id,
            "lines": [(0, 0, {
                "product_id": product_id.id,
                "qty": 1,
                "price_unit": product_id.list_price,
                "price_subtotal": product_id.list_price,
                "price_subtotal_incl": product_id.list_price,
            })],
            "amount_tax": 0.0,
            "amount_paid": product_id.list_price,
            "amount_total": product_id.list_price,
            "amount_return": 0.0,
            "partner_id": self.env["res.partner"].with_company(self.demo_data_id.company_id).search([], limit=1).id,
        })

        all_sessions = self.env["pos.session"].search([("company_id","=",self.demo_data_id.company_id.id)])
        for session in all_sessions:
            total_amount = sum(session.order_ids.mapped('amount_total'))
            session.write({'cash_register_balance_end_real': total_amount})

    def _load_field_service_data(self):
        file_path = os.path.join(self.demo_data_id.data_path, "field_service_tasks.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                user_name = row.get("user_id", "").strip()
                company_id = self.demo_data_id.company_id.id
                employee = self.env['hr.employee'].search(
                    [('name', '=', user_name), ('company_id', '=', company_id)], limit=1)
                user_exist = self.env['res.users'].search([('name', '=', user_name)], limit=1)

                if not employee:
                    continue

                user_partner_ids = employee._get_related_partners()
                user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                existing_emp_same_user = self.env['hr.employee'].search([
                    ('user_id', '=', user_exist.id),
                    ('company_id', '=', company_id)
                ], limit=1)

                if not user_exist:
                    self.env["res.users"].create({'company_id': self.demo_data_id.company_id.id,
                                                  'name': user_name,
                                                  'login': user_name,
                                                  'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                                  'work_phone': employee.work_phone,
                                                  'work_email': employee.work_email,
                                                  'employee_ids': [(4, employee.id)],
                                                  'partner_id': user_partner_id, })

                else:
                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id

                user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                  'name': user_name,
                                  'login': user_name if user_exist.id != 2 else user_exist.login,
                                  'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                  'work_phone': employee.work_phone,
                                  'work_email': employee.work_email})

                self.env['res.users'].search([]).write({'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))]})

                users = self.env["res.users"].search([('name', '=', user_name)], limit=1)
                project_id = self.env["project.project"].with_company(self.demo_data_id.company_id).search(
                    ["|", ("name", "ilike", "Field Service"), ("name", "ilike", "Services sur site"),
                     ("company_id", "=", self.demo_data_id.company_id.id)], limit=1).id
                vals = {
                    "name": row.get("name"),
                    "description": row.get("description"),
                    "project_id": project_id,
                    "date_deadline": row.get("date_deadline"),
                    "planned_date_begin": row.get("planned_date_begin"),
                    "partner_id": self.env["res.partner"].with_company(self.demo_data_id.company_id).search(
                        [("name", "ilike", row.get("partner_id"))], limit=1).id,
                    "user_ids": [(6, 0, [users.id])],
                    "company_id": self.demo_data_id.company_id.id,
                    "stage_id": self.env['project.task.type'].search(
                        [("name", "=", row.get("stage_id"))],limit=1).id,
                }

                created_task = self.env["project.task"].with_company(self.demo_data_id.company_id).create(vals)

                worksheet_template = self.env['worksheet.template'].with_company(self.demo_data_id.company_id).search([("company_id","=",self.demo_data_id.company_id.id)], limit=1)
                if not worksheet_template:
                    new_template = self.env['worksheet.template'].sudo().create({
                        'name': 'Worksheet Template 1',
                        'res_model': 'project.task',
                        'company_id': self.demo_data_id.company_id.id,
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

                created_task.worksheet_template_id = worksheet_template.id

        file_path = os.path.join(self.demo_data_id.data_path, "field_service_timesheets.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for timesheet_row in csv_reader:
                employee = self.env['hr.employee'].search([('name', '=', timesheet_row.get("employee_id")),
                                                           ('company_id', '=', self.demo_data_id.company_id.id)], limit=1)
                task_id = self.env["project.task"].search([('name', '=', timesheet_row["task_id"]),
                                                           ("company_id", "=", self.demo_data_id.company_id.id)], limit=1)

                self.env["account.analytic.line"].with_company(
                    self.demo_data_id.company_id
                ).create(
                    {"name": timesheet_row.get("name"),
                     "task_id": task_id.id,
                     "employee_id": employee.id if employee else False,
                     "unit_amount": timesheet_row.get("unit_amount"),
                     "date": timesheet_row.get("date"),
                     }
                )

    def _load_field_service_worksheets(self):
        file_path = os.path.join(self.demo_data_id.data_path, "fsm_worksheets.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            model_id = self.env["ir.model"].search([("name", "ilike", "Worksheet Template 1")], limit=1)
            for worksheet_row in csv_reader:
                task_id = self.env["project.task"].search([('name', 'ilike', worksheet_row["task_id"]),
                                                           ("company_id", "=", self.demo_data_id.company_id.id)], limit=1)

                vals = {
                    "x_service_category": worksheet_row.get("service_category"),
                    "x_work_description": worksheet_row.get("work_description"),
                    "x_equipment_details": worksheet_row.get("equipment_details"),
                    "x_work_performed": worksheet_row.get("work_performed"),
                    "x_condition_before_service": worksheet_row.get("condition_before_service"),
                    "x_condition_after_service": worksheet_row.get("condition_after_service"),
                    "x_date": worksheet_row.get("date"),
                    "x_time_spent": worksheet_row.get("time_spent"),
                    "x_customer_feedback": worksheet_row.get("customer_feedback"),
                    "x_project_task_id": task_id.id,
                }

                self.env[f"{model_id.model}"].create(vals)


    def _load_project_and_tasks(self):
        self.env["account.analytic.line"].search([("company_id", "=", self.demo_data_id.company_id.id)]).unlink()
        self.env["project.project"].search(
            ["&", "|",
             ("company_id", "=", self.demo_data_id.company_id.id),
             ("name", "=", "Field Service"),
             ("partner_id", "!=", False),
             ]).unlink()

        settings = self.env['res.config.settings'].create({
            'group_project_stages': True,
        })
        settings.execute()
        file_path = os.path.join(self.demo_data_id.data_path, "projects.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            project_stage_id = self.env['project.project.stage'].search([('name', '=', 'In Progress')], limit=1).id
            created_projects = []
            for row in csv_reader:
                user_name = row.get("user_id", "").strip()
                company_id = self.demo_data_id.company_id.id
                employee = self.env['hr.employee'].search(
                    [('name', '=', user_name), ('company_id', '=', company_id)], limit=1)
                user_exist = self.env['res.users'].search([('name', '=', user_name)], limit=1)

                if not employee:
                    continue

                user_partner_ids = employee._get_related_partners()
                user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                existing_emp_same_user = self.env['hr.employee'].search([
                    ('user_id', '=', user_exist.id),
                    ('company_id', '=', company_id)
                ], limit=1)

                if not user_exist:
                    self.env["res.users"].create({'company_id': self.demo_data_id.company_id.id,
                                                  'name': user_name,
                                                  'login': user_name,
                                                  'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                                  'work_phone': employee.work_phone,
                                                  'work_email': employee.work_email,
                                                  'employee_ids': [(4, employee.id)],
                                                  'partner_id': user_partner_id, })

                else:
                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id

                user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                      'name': user_name,
                                      'login': user_name if user_exist.id != 2 else user_exist.login,
                                      'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                      'work_phone': employee.work_phone,
                                      'work_email': employee.work_email})

                self.env['res.users'].search([]).write({'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))]})

                created_project = self.env["project.project"].with_company(
                    self.demo_data_id.company_id
                ).create(
                    {"name": row.get("name"),
                     "user_id": self.env['res.users'].search([('name','=',user_name)], limit=1).id,
                     "privacy_visibility": row.get("privacy_visibility"),
                     "stage_id": project_stage_id,
                     "company_id": self.demo_data_id.company_id.id,
                     "allow_billable": True,
                     "allow_task_dependencies": True,
                     }
                )
                created_project.write({
                    "partner_id": self.env["res.partner"].with_company(self.demo_data_id.company_id).search([('name', 'ilike', row.get("partner_id"))],
                                                                 limit=1).id if row.get("partner_id") else False,
                })
                created_projects.append(created_project.id)

        stages = ['New', 'In Progress', 'Blocked', 'Ready for Review','Done']
        for stage in stages:
            stage_exist = self.env['project.task.type'].search([('name', 'ilike', stage)],limit=1)
            if stage_exist:
                stage_exist.write({'fold': True if stage == 'Done' else False,
                                   'user_id': False,
                                   'project_ids': [(4, project_id) for project_id in created_projects],
                                   })

            else:
                self.env['project.task.type'].create({'name': stage,
                                                      'sequence': stages.index(stage) + 1,
                                                      'fold': True if stage == 'Done' else False,
                                                      'user_id': False,
                                                      'project_ids': [(4, project_id) for project_id in created_projects],
                                                      })

        file_path = os.path.join(self.demo_data_id.data_path, "tasks.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            n = 0
            for task_row in csv_reader:
                task_project_id = self.env["project.project"].search([("name", "=", task_row.get("project_id"))], limit=1)
                stage_id = self.env["project.task.type"].search([("name", "=", task_row.get("stage_id"))], limit=1)
                depend_task = self.env["project.task"].search([("project_id", "=", task_project_id.id)], limit=1)
                planned_start = task_row.get("planned_date_begin")
                deadline = task_row.get("date_deadline")

                if planned_start and deadline:
                    try:
                        planned_start_dt = datetime.strptime(planned_start, "%Y-%m-%d")
                        deadline_dt = datetime.strptime(deadline, "%Y-%m-%d")
                        if planned_start_dt >= deadline_dt:
                            continue
                    except Exception:
                        continue
                else:
                    continue

                user_name = task_row.get("user_ids", "").strip()
                company_id = self.demo_data_id.company_id.id
                employee = self.env['hr.employee'].search(
                    [('name', '=', user_name), ('company_id', '=', company_id)], limit=1)
                user_exist = self.env['res.users'].search([('name', '=', user_name)], limit=1)

                if not employee:
                    continue

                user_partner_ids = employee._get_related_partners()
                user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                existing_emp_same_user = self.env['hr.employee'].search([
                    ('user_id', '=', user_exist.id),
                    ('company_id', '=', company_id)
                ], limit=1)

                if not user_exist:
                    self.env["res.users"].create({'company_id': self.demo_data_id.company_id.id,
                                                  'name': user_name,
                                                  'login': user_name,
                                                  'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                                  'work_phone': employee.work_phone,
                                                  'work_email': employee.work_email,
                                                  'employee_ids': [(4, employee.id)],
                                                  'partner_id': user_partner_id, })

                else:
                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id

                user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                  'name': user_name,
                                  'login': user_name if user_exist.id != 2 else user_exist.login,
                                  'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                  'work_phone': employee.work_phone,
                                  'work_email': employee.work_email})

                user_ids = self.env['res.users'].search([('name', '=', task_row.get("user_ids"))], limit=1)

                self.env["project.task"].with_company(
                    self.demo_data_id.company_id
                ).create(
                    {"name": task_row.get("name"),
                     "project_id": task_project_id.id,
                     "user_ids": [(4, user_ids.id)] if user_ids else [],
                     "description": task_row.get("description"),
                     "priority": task_row.get("priority"),
                     "date_deadline": deadline,
                     "planned_date_begin": planned_start,
                     "stage_id": stage_id.id,
                     "depend_on_ids": [(6, 0, [depend_task.id])] if depend_task and 5 < n < 20 else [],
                     }
                )
                n += 1


        file_path = os.path.join(self.demo_data_id.data_path, "timesheets.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for timesheet_row in csv_reader:
                employee = self.env['hr.employee'].search([('name', '=', timesheet_row.get("employee_id")),
                                                           ('company_id','=',self.demo_data_id.company_id.id)], limit=1)
                project_id = self.env["project.project"].search([('name','=',timesheet_row["project_id"]),
                                                                       ("company_id","=",self.demo_data_id.company_id.id)], limit=1)
                task_id = self.env["project.task"].search([('name','=',timesheet_row["task_id"]),
                                                          ("company_id","=",self.demo_data_id.company_id.id)], limit=1)

                self.env["account.analytic.line"].with_company(
                    self.demo_data_id.company_id
                ).create(
                    {"name": timesheet_row.get("name"),
                     "project_id": project_id.id,
                     "task_id": task_id.id,
                     "employee_id": employee.id if employee else False,
                     "unit_amount": timesheet_row.get("unit_amount"),
                     "date": timesheet_row.get("date"),
                     }
                )

                resource_id = self.env['resource.resource'].search([('name', '=', employee.name),
                                                                    ('company_id','=',self.demo_data_id.company_id.id)],limit=1)

                if resource_id:
                    duration_hours = float(timesheet_row.get("unit_amount") or 0.0)
                    duration = timedelta(hours=duration_hours)

                    start_hour = random.randint(8, 15)
                    start_time = time(start_hour, 0)
                    start_datetime = datetime.combine(Date.from_string(timesheet_row.get("date")), start_time)
                    end_datetime = start_datetime + duration

                    if end_datetime.date() != start_datetime.date():
                        end_datetime = datetime.combine(start_datetime.date(), time(23, 59))

                    created_plan = self.env["planning.slot"].with_company(self.demo_data_id.company_id).create({
                        "resource_id": resource_id.id,
                        "start_datetime": start_datetime,
                        "end_datetime": end_datetime,
                        "allocated_hours": duration_hours,
                    })
                    created_plan.action_send()

        created_product = self.env["product.product"].with_company(self.demo_data_id.company_id).create({
            'name': f'Service Timesheet ({self.demo_data_id.company_id.name})',
            'type': 'service',
            'company_id': self.demo_data_id.company_id.id,
            'service_tracking': 'no',
            'list_price': 700.0,
            'service_policy': 'delivered_timesheet',
        })

        created_projects = self.env['project.project'].search(
            [('id', 'in', created_projects)])
        for project in created_projects:
            #create invoice for project
            sale_order = self.env["sale.order"].create({
                "partner_id": project.partner_id.id,
                "company_id": self.demo_data_id.company_id.id,
                "project_id": project.id,
                "pricelist_id": self.env['product.pricelist'].search([("company_id","=",self.demo_data_id.company_id.id)], limit=1).id,
                "order_line": [(0, 0, {
                    "product_id": created_product.id,
                    "price_unit": created_product.list_price,
                    })],
            })
            project.task_ids.write({"sale_line_id": sale_order.order_line.id})
            sale_order._create_invoices()

            tasks = self.env['project.task'].search([('project_id', '=', project.id),
                                                     ('depend_on_ids','=',False)],limit=2)
            for task in tasks:
                task.write({
                    'child_ids': [(0, 0, {
                        'name': 'Customer Feedback',
                        'project_id': project.id,
                        'stage_id': task.stage_id.id,
                        'user_ids': [(4, task.user_ids.id)],
                    })],
                })

            product_material = self.env["product.product"].with_company(self.demo_data_id.company_id).search(
                [("type", "=", "consu")], order='standard_price desc', limit=1)
            self.env["stock.quant"].search([("product_id", "=", product_material.id)]).write({"quantity": 100})

            purchase_order = self.env["purchase.order"].create({
                "partner_id": project.partner_id.id,
                "company_id": self.demo_data_id.company_id.id,
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

        pickings = (
            self.env["stock.picking"]
            .with_company(self.demo_data_id.company_id)
            .search([("sale_id.project_id","!=",False)])
        )
        for picking in pickings:
            picking.button_validate()
            if picking.state != "done":
                for move in picking.move_ids:
                    move._action_done()




    def _load_helpdesk_tickets(self):
        self.env["helpdesk.ticket"].search([('company_id', '=', self.demo_data_id.company_id.id)]).unlink()
        self.env["helpdesk.team"].search([('company_id', '=', self.demo_data_id.company_id.id)]).unlink()

        file_path = os.path.join(self.demo_data_id.data_path, "helpdesk_team.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                member_user_ids = []
                company_id = self.demo_data_id.company_id.id

                for member_name in row.get("member_ids", "").split(";"):
                    member_name = member_name.strip()
                    if not member_name:
                        continue

                    employee = self.env['hr.employee'].search(
                        [('name', '=', member_name), ('company_id', '=', company_id)], limit=1)

                    if not employee:
                        continue

                    user_exist = self.env['res.users'].search([('name', '=', member_name)], limit=1)

                    user_partner_ids = employee._get_related_partners()
                    user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                    existing_emp_same_user = self.env['hr.employee'].search([
                        ('user_id', '=', user_exist.id),
                        ('company_id', '=', company_id)
                    ], limit=1)

                    new_user = False
                    if not user_exist:
                        new_user = self.env["res.users"].create({'company_id': self.demo_data_id.company_id.id,
                                                                 'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                                                 'name': member_name,
                                                                 'login': member_name,
                                                                 'work_phone': employee.work_phone or False,
                                                                 'work_email': employee.work_email or False,
                                                                 'employee_ids': [(4, employee.id)],
                                                                 'partner_id': user_partner_id, })

                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id

                    if user_exist.id != 2:
                        user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                          'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                          'name': member_name,
                                          'login': member_name,
                                          'work_phone': employee.work_phone or False,
                                          'work_email': employee.work_email or False,
                                          'partner_id': user_partner_id, })

                    if user_exist:
                        member_user_ids.append(user_exist.id)
                    if new_user:
                        member_user_ids.append(new_user.id)

                    self.env['res.users'].search([]).write({'company_ids': [(4, self.demo_data_id.company_id.id)]})

                self.env["helpdesk.team"].with_company(
                        self.demo_data_id.company_id
                    ).create({
                        "name": row.get("name"),
                        "auto_assignment": True,
                        "company_id": self.demo_data_id.company_id.id,
                        "member_ids": [(6, 0, member_user_ids)],
                        "description": row.get("description"),
                        "use_helpdesk_timesheet": True,
                    })

        file_path = os.path.join(self.demo_data_id.data_path, "helpdesk_ticket.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            n = 0
            for ticket_row in csv_reader:
                priority = '0'
                if 3 <= n < 6:
                    priority = '1'
                elif 6 <= n < 9:
                    priority = '2'
                elif n >= 9:
                    priority = '3'

                self.env["helpdesk.ticket"].with_company(self.demo_data_id.company_id).create({
                    "name": ticket_row.get("name"),
                    "team_id": self.env["helpdesk.team"].with_company(self.demo_data_id.company_id).search(
                        [('name', '=', ticket_row.get("team_id"))], limit=1).id,
                    "user_id": self.env['res.users'].search([('name', '=', ticket_row.get("user_id"))], limit=1).id,
                    "description": ticket_row.get("description"),
                    "priority": priority,
                    "stage_id": self.env['helpdesk.stage'].search([('name', '=', ticket_row.get("stage_id"))],
                                                                  limit=1).id,
                    "partner_id": self.env["res.partner"].search([('name', 'ilike', ticket_row.get("partner_id"))],
                                                                 limit=1).id,
                })
                n += 1


        file_path = os.path.join(self.demo_data_id.data_path, "helpdesk_timesheet.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for timesheet_row in csv_reader:
                ticket_id = self.env["helpdesk.ticket"].search([('name', '=', timesheet_row.get("helpdesk_ticket_id")),
                                                                ("company_id", "=", self.demo_data_id.company_id.id)],
                                                               limit=1)

                if ticket_id.stage_id.name in ['New', 'Cancelled']:
                    continue

                self.env["account.analytic.line"].with_company(
                    self.demo_data_id.company_id
                ).create({
                    "name": timesheet_row.get("name"),
                    "helpdesk_ticket_id": ticket_id.id,
                    "project_id": ticket_id.project_id.id,
                    "employee_id": ticket_id.user_id.employee_ids.filtered(
                        lambda emp: emp.company_id == self.demo_data_id.company_id
                    ).id,
                    "unit_amount": timesheet_row.get("unit_amount"),
                    "date": timesheet_row.get("date"),
                })

        helpdesk_team = self.env['helpdesk.team'].search([('company_id', '=', self.demo_data_id.company_id.id)])[2]
        helpdesk_team.write({
            "use_rating": True,
            "allow_portal_ticket_closing": True,
            "auto_close_ticket": True,
        })
        if self.demo_data_id.stock:
            helpdesk_team.write({"use_credit_notes": True })
            helpdesk_team.write({"use_giftcards": True})
            helpdesk_team.write({"use_coupons": True})
            helpdesk_team.write({"use_product_returns": True})
            helpdesk_team.write({"use_product_replacements": True})
            helpdesk_team.write({"use_product_repairs": True})
            helpdesk_team.write({"use_fsm": True})


    def _load_event_and_venues(self):
        venue_tag = self.env["res.partner.category"].with_company(self.demo_data_id.company_id).create(
            {"name": "venue"})


        file_path = os.path.join(self.demo_data_id.data_path, "event_venues.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["country_id"] = self.env["res.country"].search([("name", "=", row.get("country"))], limit=1).id

                del row["country"]

                vals = {
                    "country_id": row.get("country_id"),
                    "name": row.get("name"),
                    "street": row.get("street"),
                    "zip": row.get("zip"),
                    "city": row.get("city"),
                    "email": row.get("email"),
                    "phone": row.get("phone"),
                    "is_company": True,
                    "company_id": self.demo_data_id.company_id.id,
                    "category_id": venue_tag,
                }
                self.env["res.partner"].with_company(self.demo_data_id.company_id).create(vals)

        file_path = os.path.join(self.demo_data_id.data_path, "events.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for event_row in csv_reader:
                user_name = event_row.get("user_id")
                company_id = self.demo_data_id.company_id.id
                user_id = (
                    self.env["res.users"]
                    .search([("name", "=", user_name)], limit=1).id)

                if not user_id:
                    employee = self.env['hr.employee'].search(
                        [('name', '=', user_name), ('company_id', '=', company_id)], limit=1)

                    if not employee:
                        continue

                    user_exist = self.env['res.users'].search([('name', '=', user_name)], limit=1)

                    user_partner_ids = employee._get_related_partners()
                    user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                    existing_emp_same_user = self.env['hr.employee'].search([
                        ('user_id', '=', user_exist.id),
                        ('company_id', '=', company_id)
                    ], limit=1)

                    new_user = False
                    if not user_exist:
                        new_user = self.env["res.users"].create({'company_id': self.demo_data_id.company_id.id,
                                                                 'name': user_name,
                                                                 'login': user_name,
                                                                 'company_ids': [
                                                                     (6, 0, (self.env["res.company"].search([]).ids))],
                                                                 'work_phone': employee.work_phone or False,
                                                                 'work_email': employee.work_email or False,
                                                                 'employee_ids': [(4, employee.id)],
                                                                 'partner_id': user_partner_id, })

                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id

                    if user_exist.id != 2:
                        user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                          'name': user_name,
                                          'login': user_name,
                                          'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                          'work_phone': employee.work_phone or False,
                                          'work_email': employee.work_email or False,
                                          'partner_id': user_partner_id, })

                    if user_exist.id == 2:
                        user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                          'name': user_name,
                                          'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                          'work_phone': employee.work_phone or False,
                                          'work_email': employee.work_email or False,
                                          'partner_id': user_partner_id, })

                    event_row["user_id"] = new_user.id if new_user else user_exist.id

                else:
                    event_row["user_id"] = user_id

                event_row["address_id"] = (
                    self.env["res.partner"].with_company(self.demo_data_id.company_id)
                    .search([("name", "ilike", event_row.get("address_id"))], limit=1).id)

                event_row["organizer_id"] = (
                    self.env["res.partner"].with_company(self.demo_data_id.company_id)
                    .search([("name", "ilike", event_row.get("organizer_id"))], limit=1).id)

                event_row["stage_id"] = (
                    self.env["event.stage"]
                    .search([("name", "=", event_row.get("stage_id"))],limit=1).id)

                event_row["lang"] = "en_US"

                vals = {
                    "name": event_row.get("name"),
                    "date_begin": event_row.get("date_begin"),
                    "date_end": event_row.get("date_end"),
                    "user_id": event_row.get("user_id"),
                    "address_id": event_row.get("address_id"),
                    "organizer_id": event_row.get("organizer_id"),
                    "stage_id": event_row.get("stage_id"),
                    "lang": event_row.get("lang"),
                }
                self.env["event.event"].with_company(self.demo_data_id.company_id).create(vals)

        for product in ['Standard', 'VIP', 'Free']:
            created_product = self.env["product.template"].with_company(
                self.demo_data_id.company_id
            ).create({
                "name": f"Event Registration - {product}",
                "type": "service",
                "list_price": 100.0 if product == 'Standard' else 250.0,
            })

            created_product.service_tracking = "event"


        event_model = self.env["event.event"]
        product_model = self.env["product.product"]
        event_products = product_model.with_company(self.demo_data_id.company_id).search(
            [("name", "ilike", "Event Registration")])

        file_path = os.path.join(self.demo_data_id.data_path, "event_tickets.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for ticket_row in csv_reader:
                ticket_event = event_model.with_company(self.demo_data_id.company_id).search(
                    [("name", "=", ticket_row.get("event_id"))], limit=1)
                event_ticket = ticket_event.search([("name", "ilike", ticket_row.get("name"))], limit=1)
                if not event_ticket:
                    product_id = event_products.search([("name", "ilike", ticket_row.get("name")),("name","ilike","Event")], limit=1)
                    ticket_row["event_id"] = ticket_event.id

                    self.env["event.event.ticket"].with_company(
                        self.demo_data_id.company_id
                    ).create({
                        "name": ticket_row.get("name"),
                        "event_id": ticket_row.get("event_id"),
                        "product_id": product_id.id,
                        "price": float(ticket_row.get("price")),
                        "seats_max": int(ticket_row.get("seats_max")),
                        "company_id": self.demo_data_id.company_id.id,
                        "start_sale_datetime": ticket_row.get("start_sale_datetime"),
                        "end_sale_datetime": ticket_row.get("end_sale_datetime"),
                    })

    def _load_event_registrations(self):
        file_path = os.path.join(self.demo_data_id.data_path, "event_registrations.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for registration_row in csv_reader:
                partner_id = (
                    self.env["res.partner"].with_company(self.demo_data_id.company_id)
                    .search([("name", "ilike", registration_row.get("partner_id"))], limit=1))

                event_id = (
                    self.env["event.event"].with_company(self.demo_data_id.company_id)
                    .search([("name", "=", registration_row.get("event_id"))], limit=1).id)

                event_ticket_id = self.env["event.event.ticket"].search([("name", "ilike", registration_row.get("event_ticket_id")),
                                                           ('event_id', '=', event_id)], limit=1).id


                registration_row["partner_id"] = partner_id.id
                registration_row["event_id"] = event_id
                registration_row["event_ticket_id"] = event_ticket_id
                registration_row["company_name"] = partner_id.parent_id.name

                vals = {
                    "event_id": registration_row.get("event_id"),
                    "partner_id": registration_row.get("partner_id"),
                    "event_ticket_id": registration_row.get("event_ticket_id"),
                    "company_name": registration_row.get("company_name"),
                }
                created_registration = self.env["event.registration"].with_company(self.demo_data_id.company_id).create(vals)

                if created_registration.event_id.stage_id.name in ["Announced","Ended"]:
                    sale_order_id = self.env["sale.order"].with_company(
                        self.demo_data_id.company_id
                    ).create({
                        "partner_id": partner_id.id,
                        "order_line": [(0, 0, {
                            "name": "Event Registration",
                            "product_id": created_registration.event_ticket_id.product_id.id,
                            "product_uom_qty": 1,
                            "price_unit": created_registration.event_ticket_id.price,
                            "event_id": created_registration.event_id.id,
                            "event_ticket_id": created_registration.event_ticket_id.id,
                        })],
                    })

                    created_registration.sale_order_id = sale_order_id.id

                if created_registration.event_id.stage_id.name == "Ended":
                    created_registration.action_set_done()


    def _load_marketing_campaigns(self):
        admin_user = self.env["res.users"].search([("id", "=", 2)], limit=1)
        if not admin_user.email:
            admin_employee = admin_user.employee_ids.filtered(lambda l: l.company_id == self.demo_data_id.company_id)
            if admin_employee:
                admin_user.write({'email': admin_employee.work_email,
                                  'phone': admin_employee.work_phone})
                admin_user.partner_id.write({'email': admin_employee.work_email,
                                             'phone': admin_employee.work_phone})
            else:
                admin_user.write({'email': 'test@user.com'})

        self.env['marketing.campaign'].with_company(self.demo_data_id.company_id).search([]).unlink()

        file_path = os.path.join(self.demo_data_id.data_path, "mailing_list.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for mailing_row in csv_reader:
                mailing_row["country_id"] = self.env["res.country"].search([("name", "=", mailing_row.get("country_id"))],
                                                                           limit=1).id

                vals = {
                    "name": mailing_row.get("name"),
                    "email": mailing_row.get("email"),
                    "company_name": mailing_row.get("company_name"),
                    "country_id": mailing_row.get("country_id"),
                }

                self.env["mailing.contact"].with_company(self.demo_data_id.company_id).create(vals)

        self.env['marketing.campaign'].with_company(
            self.demo_data_id.company_id)._get_marketing_template_commercial_prospection_values()

        self.env['marketing.campaign'].with_company(
            self.demo_data_id.company_id)._get_marketing_template_prioritize_hot_leads_values()

        self.env['marketing.campaign'].with_company(
            self.demo_data_id.company_id)._get_marketing_template_double_opt_in_values()

        self.env['marketing.campaign'].with_company(
            self.demo_data_id.company_id)._get_marketing_template_purchase_followup()

        all_campaigns = self.env['marketing.campaign'].with_company(
            self.demo_data_id.company_id).search([])
        all_campaigns.domain = []

        campaigns = self.env['marketing.campaign'].with_company(
            self.demo_data_id.company_id).search([],limit=3)

        campaigns.action_start_campaign()
        campaigns.sync_participants()


    def _load_articles(self):
        image_dir = self.demo_data_id.data_path
        images_to_delete = {
            "sales_process.jpg",
            "hr_process.jpg",
            "supply_chain_process.jpg",
            "administrative_process.jpg",
        }

        for image_name in images_to_delete:
            image_path = os.path.join(image_dir, image_name)
            if os.path.isfile(image_path):
                try:
                    os.remove(image_path)
                    _logger.info("Deleted image: %s", image_path)
                except Exception as e:
                    _logger.warning("Could not delete image %s: %s", image_path, e)

        image_agent = ImageAgent(self.demo_data_id.api_key_id.name)
        image_agent.get_agent()
        file_path = os.path.join(self.demo_data_id.data_path, "knowledge_articles.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                article = row.get("name")
                safe_name = article.lower().replace(" ", "_")

                def _generate_image(image_suffix, prompt):
                    filename = f"{safe_name}_{image_suffix}.jpg"
                    image_path = os.path.join(self.demo_data_id.data_path, filename)

                    if not os.path.isfile(image_path):
                        image_agent.save_image(prompt, image_path)

                    if os.path.isfile(image_path):
                        with open(image_path, "rb") as image:
                            attachment = self.env["ir.attachment"].with_company(
                                self.demo_data_id.company_id
                            ).create({
                                "name": filename,
                                "datas": base64.b64encode(image.read()),
                                "res_model": "knowledge.article",
                                "mimetype": "image/jpeg",
                                "type": "binary",
                            })
                            if attachment and attachment.checksum:
                                base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
                                return f"{base_url}/{attachment.image_src}"

                    return False

                prompt_1 = (
                    image_prompts.ARTICLE_IMAGES_PROMPT
                    .replace("__name__", self.demo_data_id.name)
                    .replace("__article_title__", article)
                )

                prompt_2 = (
                    image_prompts.ARTICLE_IMAGES_PROMPT
                    .replace("__name__", self.demo_data_id.name)
                    .replace("__article_title__", f"{article} illustration")
                )

                try:
                    image_url_1 = _generate_image("1", prompt_1)
                    image_url_2 = _generate_image("2", prompt_2)
                except Exception as e:
                    self.logs = (self.logs or "") + f"\nImage generation failed for article '{article}': {e}"
                    image_url_1 = image_url_2 = False

                body_html = f"<h1>{row.get('title') if row.get('title') else row.get('name')}</h1>"

                if image_url_1:
                    body_html += (f"<p><img src='{image_url_1}' "
                                  f"class='img img-fluid o_we_custom_image'></p>")

                body_html += (f"<p>{row.get('intro')}</p></br>"
                              f"<p>{row.get('body')}</p></br>")

                if image_url_2:
                    body_html += (f"<p><img src='{image_url_2}' "
                                  f"class='img img-fluid o_we_custom_image'></p>")

                body_html += f"<p>{row.get('conclusion')}</p>"

                del row["intro"]
                del row["conclusion"]
                del row["title"]

                row["is_article_visible_by_everyone"] = True

                vals = {"name": row.get("name") + f" ({self.demo_data_id.name.upper()})",
                        "body": body_html,
                        "is_article_visible_by_everyone": True,}

                self.env["knowledge.article"].with_company(self.demo_data_id.company_id).create(vals)

    # for leaves creation as sometimes the AI creates leaves on non-working days, weekends.
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
        working_days = calendar.attendance_ids.mapped('dayofweek')
        if str(weekday) not in working_days:
            return True

        public_holiday_domain = [('resource_id', '=', False),
                                 ('calendar_id', 'in', [calendar.id, False]),
                                 ('date_from', '<=', day_dt),
                                 ('date_to', '>=', day_dt), ]

        is_public_holiday = self.env['resource.calendar.leaves'].search_count(public_holiday_domain)

        if is_public_holiday and is_single_day_leave:
            return True

        leave_domain = [('calendar_id', 'in', [calendar.id, False]),
                        ('date_from', '<=', day_dt),
                        ('date_to', '>=', day_dt), ]

        if self.env['resource.calendar.leaves'].search_count(leave_domain):
            return True

        return False

    def _load_time_off_requests(self):
        employee_id = self.env["hr.employee"].search(
            [("name", "ilike", self.env.user.name),
             ("company_id", "=", self.demo_data_id.company_id.id)])
        if not employee_id.user_id:
            employee_id.write({"user_id": self.env.user.id})

        hr_leave_list = self.env["hr.leave"].search([("company_id", "=", self.demo_data_id.company_id.id)])
        for hr_leave in hr_leave_list:
            if hr_leave.state == "approved":
                hr_leave.action_refuse()
            hr_leave.unlink()

        leave_allocation_list = self.env["hr.leave.allocation"].search(
            [("employee_company_id", "=", self.demo_data_id.company_id.id)])
        for leave_allocation in leave_allocation_list:
            if leave_allocation.state == "validate":
                leave_allocation.action_refuse()
            leave_allocation.unlink()

        self.env["hr.leave.mandatory.day"].with_company(self.demo_data_id.company_id).search([("company_id","=",self.demo_data_id.company_id.id)]).unlink()
        self.env["resource.calendar.leaves"].with_company(self.demo_data_id.company_id).search([("company_id","=",self.demo_data_id.company_id.id)]).unlink()
        if self.demo_data_id.project:
            self.env["account.analytic.line"].with_company(self.demo_data_id.company_id).search(
                [("name", "ilike", "Time Off"), ("global_leave_id", "=", False), ("holiday_id", "=", False)]).unlink()

        file_path = os.path.join(self.demo_data_id.data_path, "mandate_days.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for mandate_row in csv_reader:
                mandate_row["start_date"] = mandate_row["date"]
                mandate_row["end_date"] = mandate_row["date"]

                del mandate_row["date"]

                self.env["hr.leave.mandatory.day"].with_company(self.demo_data_id.company_id).create(mandate_row)



        file_path = os.path.join(self.demo_data_id.data_path, "public_holidays.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                base_date = datetime.strptime(row["date_from"], "%Y-%m-%d")

                date_from = datetime.combine(base_date.date(), time.min)
                date_to = date_from + timedelta(hours=12)

                date_from_str = Datetime.to_string(date_from)
                date_to_str = Datetime.to_string(date_to)

                existing_holiday = self.env["resource.calendar.leaves"].with_company(
                    self.demo_data_id.company_id).search(
                    [('date_from', '<', date_to_str), ('date_to', '>', date_from_str),
                     ('company_id', '=', self.demo_data_id.company_id.id), ], limit=1)

                if existing_holiday:
                    continue

                row["date_from"] = Datetime.to_string(date_from)
                row["date_to"] =  Datetime.to_string(date_to)

                self.env["resource.calendar.leaves"].with_company(
                    self.demo_data_id.company_id
                ).create(row)

        accrual_leave_type_id = self.env["hr.leave.type"].with_company(self.demo_data_id.company_id).search(
            [("name", "=", "Accrual Plan Leave")])

        if not accrual_leave_type_id:
            accrual_leave_type_id = self.env["hr.leave.type"].with_company(self.demo_data_id.company_id).create({
                "name": "Accrual Plan Leave",
                "requires_allocation": True,
                "leave_validation_type": "no_validation",
                "allocation_validation_type": "no_validation",
            })

        self.env["hr.leave.accrual.plan"].with_company(self.demo_data_id.company_id).search([]).unlink()

        accrual_plan_id = self.env["hr.leave.accrual.plan"].with_company(self.demo_data_id.company_id).create({
            "name": "Standard Accrual Plan",
            "company_id": self.demo_data_id.company_id.id,
        })

        self.env["hr.leave.accrual.level"].with_company(self.demo_data_id.company_id).create({
            "accrual_plan_id": accrual_plan_id.id,
            "added_value": 2,
            "frequency": "monthly",
        })

        accrual_leave_id = self.env["hr.leave.allocation"].with_company(self.demo_data_id.company_id).create({
            "name": "Initial Accrual Allocation",
            "holiday_status_id": accrual_leave_type_id.id,
            "allocation_type": "accrual",
            "accrual_plan_id": accrual_plan_id.id,
            "number_of_days": 22,
            "employee_id": self.env["hr.employee"].sudo().search(
                [("name", "ilike", self.env.user.name), ("company_id", "=", self.demo_data_id.company_id.id)]).id,
            "date_from": f"{fields.Date.today().year}-01-01",
            "date_to": f"{fields.Date.today().year}-12-31",
        })
        accrual_leave_id._compute_description()

        self.env["hr.leave.type"].with_company(self.demo_data_id.company_id).search([]).write(
            {"leave_validation_type": "hr"})

        file_path = os.path.join(self.demo_data_id.data_path, "time_off_allocation.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for allocation_row in csv_reader:

                employee = self.env['hr.employee'].search([('name', '=', allocation_row["employee_id"]),
                                                           ('company_id','=',self.demo_data_id.company_id.id)], limit=1)
                if not employee:
                    continue
                allocation_row["employee_id"] = employee.id
                current_year = fields.Date.today().year
                allocation_row["date_from"] = f"{current_year}-01-01"

                allocation_row["holiday_status_id"] = self.env['hr.leave.type'].with_company(
                    self.demo_data_id.company_id).search([('name', '=', allocation_row["holiday_status_id"])],
                                                         limit=1).id

                created_allocation = self.env["hr.leave.allocation"].with_company(
                    self.demo_data_id.company_id
                ).create(allocation_row)
                created_allocation.action_approve()


        file_path = os.path.join(self.demo_data_id.data_path, "time_off_requests.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                employee = self.env['hr.employee'].search([('name', '=', row["employee_id"]),
                                                           ('company_id','=',self.demo_data_id.company_id.id)], limit=1)
                if not employee:
                    continue

                leave_type = self.env['hr.leave.type'].with_company(self.demo_data_id.company_id).search(
                    [('name', '=', row["holiday_status_id"])], limit=1)

                if not leave_type:
                    continue

                date_from = row['request_date_from']
                leave_data = leave_type.get_allocation_data(employee, date_from)

                if leave_type.requires_allocation != 'no' and not leave_data.get(employee):
                    continue

                if leave_type.requires_allocation != 'no':
                    allocation_info = leave_data[employee][0][1]
                    remaining = allocation_info.get('virtual_remaining_leaves', 0.0)

                    max_excess = leave_type.max_allowed_negative
                    if leave_data[employee] and leave_data[employee][0][1]['virtual_remaining_leaves'] < -max_excess:
                        continue

                    if not leave_type.allows_negative and remaining <= 0:
                        continue

                    if leave_type.allows_negative:
                        max_negative = leave_type.max_allowed_negative
                        if remaining < -max_negative:
                            continue

                row["employee_id"] = employee.id
                row["holiday_status_id"] = leave_type.id

                date_from = fields.Datetime.from_string(row["request_date_from"])
                date_to = fields.Datetime.from_string(row["request_date_to"])

                leave_dates = self._get_dates_between(date_from, date_to)

                non_working_days = [day for day in leave_dates if self._is_non_working_day(employee, day)]

                total_days = len(leave_dates)
                non_working_count = len(non_working_days)
                create_leave = False

                if total_days == 1:
                    if non_working_count == 0:
                        create_leave = True

                elif total_days == 2:
                    if non_working_count < 2:
                        create_leave = True

                else:
                    create_leave = True

                if not create_leave:
                    continue

                if self._has_overlapping_leave(employee, date_from, date_to):
                    continue

                created_leave = self.env["hr.leave"].with_company(self.demo_data_id.company_id).create(row)

                today = fields.Date.today()
                leave_date = created_leave.date_from.date()

                if (leave_date < today.replace(
                        day=1) and created_leave.holiday_status_id.leave_validation_type != 'no_validation'):
                    created_leave.action_approve()

    def _load_recruitment_data(self):
        file_path = os.path.join(self.demo_data_id.data_path, "job_positions.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            employee_ids = self.env["hr.employee"].search([('company_id', '=', self.demo_data_id.company_id.id)])
            for employee_id in employee_ids:
                user_name = employee_id.name
                company_id = self.demo_data_id.company_id.id
                user_id = (
                    self.env["res.users"]
                    .search([("name", "=", user_name)], limit=1).id)

                if not user_id:
                    employee = self.env['hr.employee'].search(
                        [('name', '=', user_name), ('company_id', '=', company_id)], limit=1)

                    if not employee:
                        continue

                    user_exist = self.env['res.users'].search([('name', '=', user_name)], limit=1)

                    user_partner_ids = employee._get_related_partners()
                    user_partner_id = user_partner_ids.filtered(lambda p: p.company_id.id == company_id)[:1].id

                    existing_emp_same_user = self.env['hr.employee'].search([
                        ('user_id', '=', user_exist.id),
                        ('company_id', '=', company_id)
                    ], limit=1)

                    if not user_exist:
                        self.env["res.users"].create({'company_id': self.demo_data_id.company_id.id,
                                                      'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                                      'name': user_name,
                                                      'login': user_name,
                                                      'work_phone': employee.work_phone or False,
                                                      'work_email': employee.work_email or False,
                                                      'employee_ids': [(4, employee.id)],
                                                      'partner_id': user_partner_id, })

                    if not employee.user_id and not existing_emp_same_user:
                        employee.user_id = user_exist.id

                    if user_exist.id != 2:
                        user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                          'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                          'name': user_name,
                                          'login': user_name,
                                          'work_phone': employee.work_phone or False,
                                          'work_email': employee.work_email or False,
                                          'partner_id': user_partner_id, })

                    if user_exist.id == 2:
                        user_exist.write({'company_id': self.demo_data_id.company_id.id,
                                          'name': user_name,
                                          'company_ids': [(6, 0, (self.env["res.company"].search([]).ids))],
                                          'work_phone': employee.work_phone or False,
                                          'work_email': employee.work_email or False,
                                          'partner_id': user_partner_id, })
            for row in csv_reader:
                row["user_id"] = self.env["res.users"].search([("name","=",row.get("user_id"))]).id

                interviewer_ids = []
                raw_interviewers = row.get("interviewer_ids", "")

                for interviewer in raw_interviewers.split(";"):
                    interviewer = interviewer.strip()
                    if not interviewer:
                        continue

                    user_exist = self.env['res.users'].search([
                        ('name', 'ilike', interviewer)
                    ], limit=1)

                    if user_exist:
                        interviewer_ids.append(user_exist.id)


                self.env["hr.job"].with_company(self.demo_data_id.company_id).create({
                    "name": row.get("name"),
                    "department_id": self.env['hr.department'].with_company(
                        self.demo_data_id.company_id).search([('name', '=', row.get("department_id"))],
                                                            limit=1).id,
                    "description": row.get("description"),
                    "user_id": row.get("user_id"),
                    "interviewer_ids": [(6, 0, interviewer_ids)],
                    "expected_degree": self.env['hr.recruitment.degree'].search([('name', '=', row.get("expected_degree"))],
                                                                              limit=1).id,
                    "contract_type_id": self.env['hr.contract.type'].with_company(
                        self.demo_data_id.company_id).search([('name', '=', row.get("contract_type_id"))],
                                                            limit=1).id,
                    "no_of_recruitment": row.get("no_of_recruitment"),
                    "company_id": self.demo_data_id.company_id.id,
                })

        file_path = os.path.join(self.demo_data_id.data_path, "job_applications.csv")
        with open(file_path, "r", encoding="utf-8-sig") as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for applicant_row in csv_reader:
                job_id = self.env["hr.job"].with_company(
                        self.demo_data_id.company_id).search([('name', '=', applicant_row.get("job_id"))],
                                                             limit=1)
                created_applicant = self.env["hr.applicant"].with_company(self.demo_data_id.company_id).create({
                    "partner_name": applicant_row.get("partner_name"),
                    "email_from": applicant_row.get("email_from"),
                    "partner_phone": applicant_row.get("partner_phone"),
                    "job_id": job_id.id,
                    "user_id": self.env['res.users'].search([('name', '=', applicant_row.get("user_id"))],
                                                            limit=1).id,
                    "stage_id": self.env['hr.recruitment.stage'].with_company(
                        self.demo_data_id.company_id).search([('name', '=', "New")],
                                                             limit=1).id,
                    "type_id": self.env['hr.recruitment.degree'].with_company(
                        self.demo_data_id.company_id).search([('name', '=', applicant_row.get("type_id"))],
                                                             limit=1).id,
                    "interviewer_ids": job_id.interviewer_ids,
                    "availability": applicant_row.get("availability"),
                    "applicant_notes": applicant_row.get("applicant_notes"),
                    "salary_expected": applicant_row.get("salary_expected"),
                    "salary_proposed": applicant_row.get("salary_proposed"),
                    "priority": applicant_row.get("priority"),
                    "company_id": self.demo_data_id.company_id.id,
                })

                created_applicant.write({
                    "stage_id": self.env['hr.recruitment.stage'].with_company(
                        self.demo_data_id.company_id).search([('name', '=', applicant_row.get("stage_id"))],
                                                             limit=1).id,
                })



    def _load_website_data(self):
        vals = {
            "target_url": self.demo_data_id.website_url,
            "import_products": True,
        }
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
        "Manufacture: Work Orders": _load_work_orders,
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
            self.with_company(self.demo_data_id.company_id).function_dict[self.name](
                self
            )
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
            if self.env["website_generator.request"].search(
                [("target_url", "ilike", self.demo_data_id.website_url)]).status == "done":
                self.state = "ok"
                return True
            else:
                self.state = "processing"
                return True

        if self.state == "ok" and self.name == "Inventory":
            if self.env["ir.module.module"].search([("name", "=", "purchase")], limit=1).state in ["installed",
                                                                                                   "to_upgrade",
                                                                                                   "to_remove"]:
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

