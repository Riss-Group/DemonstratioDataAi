import csv
import os
import re
import logging
from odoo import fields, models, api
from odoo.exceptions import UserError
from . import cap_api_call
from mistralai import Mistral
from mistralai.models import ToolFileChunk
from PIL import Image
import base64

_logger = logging.getLogger(__name__)

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
                file_byte = self.client.files.download(file_id=chunk.file_id).read()
                return file_byte

    def save_image(self, prompt: str, path_name: str):
        file_byte = self.gen_image(prompt)
        with open(os.path.join(path_name), "wb") as image_file:
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

    @api.onchange("prompt")
    def _on_change_prompt(self):
        self.state = "in_line"
        self.demo_data_id.generation_in_progress = True

    def generate_data(self, dict_fill_in: dict, api_key: str):
        if not self.file_to_load or not self.prompt:
            _logger.info("No generation for this action")
            return
        _logger.info("Starting generation for", self.name)

        for element in dict_fill_in:
            self.prompt = self.prompt.replace(element, dict_fill_in[element])
        result = cap_api_call.mistral_api_call(
            MISTRAL_SMALL, self.prompt, api_key, self.demo_data_id.lang
        )
        text = result.choices[0].message.content
        regex_pattern = r"```csv(.+?)```"
        files_to_load = self.file_to_load.split(";")
        is_csv = any(file[len(file) - 4 :] == ".csv" for file in files_to_load)
        # if not files_to_load.csv : Put the whole content in the file, else: do the whole ```csv match thing
        if not is_csv:
            for i in range(len(files_to_load)):
                with open(
                    os.path.join(self.demo_data_id.data_path, files_to_load[i]),
                    "w",
                    encoding="utf-8-sig",
                ) as file:
                    file.write(text)
            return

        matches = re.findall(regex_pattern, text, flags=re.DOTALL)[: len(files_to_load)]
        for i in range(len(files_to_load)):
            with open(
                os.path.join(self.demo_data_id.data_path, files_to_load[i]),
                "w",
                encoding="utf-8-sig",
            ) as csv_file:
                csv_file.write(matches[i].strip())
        _logger.info("Finished generation for", self.name)

    def _load_general_information(self):
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
            is_stock: bool = (
                True
                if (
                    self.env["ir.module.module"]
                    .search([("name", "=", "stock")], limit=1)
                    .state
                    in ["installed", "to_remove", "to_upgrade"]
                    and self.demo_data_id.stock
                )
                else False
            )
            with open(
                os.path.join(self.demo_data_id.data_path, file_name),
                mode="r",
                encoding="utf-8-sig",
            ) as csv_file:
                csv_reader = csv.DictReader(csv_file)
                for row in csv_reader:
                    if "invoice_policy" in self.env["product.product"]._fields:
                        row["invoice_policy"] = "delivery"
                    if row["type"] == "consu" and is_stock:
                        row["is_storable"] = True
                    if self.demo_data_id.bool_company_restricted:
                        row["company_id"] = self.demo_data_id.company_id.id
                    self.env["product.template"].with_company(
                        self.demo_data_id.company_id
                    ).create(row)

        _load_product("customer_products.csv")
        _load_product("supplier_products.csv")

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
                row["customer_rank"] = 1
                row["category_id"] = customer_tag
                if self.demo_data_id.bool_company_restricted:
                    row["company_id"] = self.demo_data_id.company_id.id
                self.env["res.partner"].with_company(
                    self.demo_data_id.company_id
                ).create(row)

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
                row["supplier_rank"] = 1
                row["category_id"] = supplier_tag
                if self.demo_data_id.bool_company_restricted:
                    row["company_id"] = self.demo_data_id.company_id.id
                self.env["res.partner"].with_company(
                    self.demo_data_id.company_id
                ).create(row)

    def _load_bills(self):
        _logger.info(
            "fiscal localisation applied:", self.demo_data_id.company_id.chart_template
        )
        account_move_list = self.env["account.move"].search([])
        for account_move in account_move_list:
            if account_move.state == "posted":
                account_move.button_draft()
        account_move_list.unlink()

        def create_bills(file_name):
            with open(
                os.path.join(self.demo_data_id.data_path, file_name),
                mode="r",
                encoding="utf-8-sig",
            ) as csv_file:
                csv_reader = csv.DictReader(csv_file)
                for row in csv_reader:
                    row["partner_id"] = (
                        self.env["res.partner"]
                        .search([("name", "like", row["partner_name"])], limit=1)
                        .id
                    )
                    row["invoice_date"] = row["date"]
                    dict_line = {
                        "product_id": row["invoice_line_ids/product_name"],
                        "quantity": row["invoice_line_ids/quantity"],
                        "price_unit": row["invoice_line_ids/price_unit"],
                    }
                    del (
                        row["invoice_line_ids/product_name"],
                        row["invoice_line_ids/quantity"],
                        row["invoice_line_ids/price_unit"],
                        row["partner_name"],
                    )
                    dict_line["product_id"] = (
                        self.env["product.product"]
                        .search([("name", "like", dict_line["product_id"])], limit=1)
                        .id
                    )
                    move_id = (
                        self.env["account.move"]
                        .with_company(self.demo_data_id.company_id)
                        .create(row)
                    )
                    dict_line["move_id"] = move_id.id
                    self.env["account.move.line"].with_company(
                        self.demo_data_id.company_id
                    ).create(dict_line)
                    move_id.action_post()

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
                    order="id",
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

    def _load_data_crm(self):
        with open(
            os.path.join(self.demo_data_id.data_path, "crm_leads.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["stage_id"] = (
                    self.env["crm.stage"]
                    .search([("name", "=", row["stage_name"])], limit=1)
                    .id
                )
                del row["stage_name"]
                row["partner_id"] = (
                    self.env["res.partner"]
                    .search([("name", "=", row["partner_id"])], limit=1)
                    .id
                )
                self.env["crm.lead"].with_company(self.demo_data_id.company_id).create(
                    row
                )
        with open(
            os.path.join(self.demo_data_id.data_path, "crm_activities.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["activity_type_id"] = (
                    self.env["mail.activity.type"]
                    .search([("name", "=", row["activity_type_id"])])
                    .id
                )
                row["res_model"] = "crm.lead"
                row["res_model_id"] = self.env["ir.model"]._get("crm.lead").id
                row["res_id"] = (
                    self.env["crm.lead"]
                    .search([("name", "=", row["lead_name"])], limit=1)
                    .id
                )
                del row["lead_name"]
                self.env["mail.activity"].with_company(
                    self.demo_data_id.company_id
                ).create(row)

    def _load_data_sales(self):
        is_stock: bool = (
            True
            if (
                self.env["ir.module.module"]
                .search([("name", "=", "stock")], limit=1)
                .state
                in ["installed", "to_remove", "to_upgrade"]
                and self.demo_data_id.stock
            )
            else False
        )
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
                row["partner_id"] = (
                    self.env["res.partner"]
                    .search([("name", "like", row["partner_id"])], limit=1)
                    .id
                )
                if n < 3:
                    row["state"] = "draft"
                elif n < 5:
                    row["state"] = "sent"
                else:
                    row["state"] = "sale"
                dict_line = {
                    "product_id": row["order_line/product_id"],
                    "product_uom_qty": row["order_line/quantity"],
                    "price_unit": row["order_line/price_unit"],
                }
                del (
                    row["order_line/product_id"],
                    row["order_line/quantity"],
                    row["order_line/price_unit"],
                )
                product = self.env["product.product"].search(
                    [("name", "=", dict_line["product_id"])], limit=1
                )
                dict_line["product_id"] = product.id
                order_id = (
                    self.env["sale.order"]
                    .with_company(self.demo_data_id.company_id)
                    .create(row)
                )
                if n >= 10:
                    dict_line["qty_delivered"] = dict_line["product_uom_qty"]
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
                    try:
                        invoices = order_id._create_invoices()
                        invoices.action_post()
                    except Exception as e:
                        _logger.error("Invoice Exception:\n", e)
                n += 1

    def _load_data_purchase(self):
        is_stock: bool = (
            True
            if (
                self.env["ir.module.module"]
                .search([("name", "=", "stock")], limit=1)
                .state
                in ["installed", "to_remove", "to_upgrade"]
                and self.demo_data_id.stock
            )
            else False
        )
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
                    .search([("name", "=", "Stock")], limit=1)
                    .id
                )
            for row in csv_reader:
                row["partner_id"] = (
                    self.env["res.partner"]
                    .search([("name", "like", row["partner_id"])], limit=1)
                    .id
                )
                if is_stock:
                    row["picking_type_id"] = picking_type_id
                if n < 3:
                    row["state"] = "draft"
                elif n < 5:
                    row["state"] = "sent"
                else:
                    row["state"] = "purchase"
                dict_line = {
                    "product_id": row["order_line/product_id"],
                    "product_qty": row["order_line/quantity"],
                    "price_unit": row["order_line/price_unit"],
                }
                del (
                    row["order_line/product_id"],
                    row["order_line/quantity"],
                    row["order_line/price_unit"],
                )
                product = self.env["product.product"].search(
                    [("name", "=", dict_line["product_id"])], limit=1
                )
                dict_line["product_id"] = product.id
                order_id = (
                    self.env["purchase.order"]
                    .with_company(self.demo_data_id.company_id)
                    .create(row)
                )
                dict_line["order_id"] = order_id.id
                if product.type == "consu":
                    if n >= 10:
                        dict_line["qty_received"] = dict_line["product_qty"]
                    else:
                        dict_line["qty_received"] = 0
                self.env["purchase.order.line"].with_company(
                    self.demo_data_id.company_id
                ).create(dict_line)
                if n > 15 and product.type == "consu":
                    order_id.action_create_invoice()
                    bill = (
                        self.env["account.move"]
                        .with_company(self.demo_data_id.company_id)
                        .search([("state", "=", "draft")], limit=1)
                    )
                    bill.invoice_date = bill.date
                    bill.action_post()
                n += 1

    def _load_data_stock(self):
        warehouse = self.env["stock.warehouse"].search(
            [("name", "=", self.env.user.company_id.name)], limit=1
        )
        if not warehouse:
            warehouse = (
                self.env["stock.warehouse"]
                .with_company(self.demo_data_id.company_id)
                .create({"name": self.env.user.company_id.name, "code": "STOCK"})
            )

        self.env["stock.picking.type"].with_company(
            self.demo_data_id.company_id
        ).create(
            {
                "name": "Stock",
                "sequence_code": "STOCK",
            }
        )
        with open(
            os.path.join(self.demo_data_id.data_path, "stock.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["product_id"] = (
                    self.env["product.product"]
                    .search([("name", "=", row["product_id"])], limit=1)
                    .id
                )
                row["location_id"] = warehouse.lot_stock_id.id
                row["quantity"] = row["inventory_quantity"]
                self.env["stock.quant"].with_company(
                    self.demo_data_id.company_id
                ).create(row)
        config_parameter = self.env["ir.config_parameter"].sudo()
        config_parameter.set_param("stock.sms_confirmation", "False")

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
                ).create(row)
        with open(
            os.path.join(self.demo_data_id.data_path, "employees.csv"),
            mode="r",
            encoding="utf-8-sig",
        ) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                row["parent_id"] = (
                    self.env["hr.employee"]
                    .search([("name", "=", row["parent_id"])], limit=1)
                    .id
                )
                row["department_id"] = (
                    self.env["hr.department"]
                    .search([("name", "=", row["department_id"])], limit=1)
                    .id
                )
                self.env["hr.employee"].with_company(
                    self.demo_data_id.company_id
                ).create(row)


    def _load_pictures_hr(self):

        def crop_and_save(path: str, employee) -> str:
            im = Image.open(path)
            width, height = im.size
            im = im.crop((width / 4, 0, (width / 4) * 3, height))
            cropped = os.path.join(
                self.demo_data_id.data_path,
                ("cropped_" +  employee.name.lower().replace(" ", "_") + ".jpg").strip(),
            )
            im.save(cropped)
            return cropped

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
                (employee.gender[0] + '_' + employee.name.lower().replace(" ", "_") + ".jpg").strip(),
            )
            if employee.gender == 'male':
                male_employee_picture.append(path)
            if employee.gender == 'female':
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
        print(male_employee_picture)
        print(female_employee_picture)
        def assign_picture(iterator: int, path_list: list[str], employee) -> int:
            if not path_list:
                return 0
            cropped = crop_and_save(path_list[iterator % len(path_list)], employee)
            with open(cropped, "rb") as image:
                employee.image_1920 = base64.b64encode(image.read())
            return 1

        for employee in self.env["hr.employee"].search([('id', 'not in', id_list)]):
            if employee.gender == 'male':
                nb_m += assign_picture(nb_m, male_employee_picture, employee)
            if employee.gender == 'female':
                nb_f += assign_picture(nb_f, female_employee_picture, employee)


    function_dict = {
        "General Information": _load_general_information,
        "Background Image": _load_background,
        "Products": _load_products,
        "Customers and Suppliers": _load_partners,
        "Customer and Supplier bills": _load_bills,
        "CRM": _load_data_crm,
        "Inventory": _load_data_stock,
        "Sales": _load_data_sales,
        "Purchase": _load_data_purchase,
        "Employees": _load_data_hr,
        "Employees Pictures": _load_pictures_hr,
    }

    def load_data(self):
        _logger.info("Loading data for", self.name)
        if self.function_dict[self.name]:
            self.with_company(self.demo_data_id.company_id).function_dict[self.name](
                self
            )
            self.state = "ok"
        else:
            self.state = "failed"
            raise UserError("Tried generating data for a non-supported module")
        _logger.info("Finished loading data for", self.name)

    def exec_action(self, nb_tries=0):
        if self.state == "ok":
            return False
        self.state = "processing"
        self.nb_tries += 1
        try:
            self.generate_data(
                self.demo_data_id.get_values_dict(), self.demo_data_id.api_key_id.name
            )
            self.load_data()
        except Exception as e:
            _logger.error(e)
            self.logs = e
            self.state = "failed"
            self.env.cr.commit()
            if nb_tries < 3:
                self.exec_action(nb_tries + 1)
            else:
                raise UserError(
                    'Failed to execute the action "%s" for more than 3 tries, stopping execution'
                    % self.name
                )
        self.state = "ok"
        self.env.cr.commit()
        return True
