import logging
import os
from pathlib import Path

from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError, ValidationError
from datetime import datetime

from .prompts import base_prompts
from .prompts import image_prompts
from .prompts import module_prompts

_logger = logging.getLogger(__name__)


class DemoData(models.Model):
    _name = "demo.data"
    _description = "Generate Demo data and load it to the database"

    def _default_api_key(self):
        return self.env["api.key"].search([], limit=1, order="id desc").id

    name = fields.Char(
        string="Company Name",
        required=True,
        help="The name of the company which you want to generate data for",
        copy=False,
    )

    is_active = fields.Boolean(default=False)
    email = fields.Char(
        string="Email", help="The email that will be defined on creation of the company"
    )
    city = fields.Char(
        string="City",
        required=True,
        help="The company made will be based in this city. Its customers and suppliers may also be in this city or around",
    )
    logo = fields.Binary(string="Company Logo", readonly=False)
    lang = fields.Selection(
        string="Language",
        selection=[("en_US", "English (US)"), ("fr_FR", "French / Français")],
        default="fr_FR",
    )
    country_id = fields.Many2one(
        "res.country",
        string="Country",
        help="The company created will be based in this country and its customers will most likely be based there as well",
        required=True,
    )
    api_key_id = fields.Many2one(
        "api.key",
        string="API Key",
        required=True,
        default=_default_api_key,
        help="Generate a Mistral AI Api key to put here",
    )
    demo_date = fields.Date(
        string="Demo Date",
        default=datetime.today(),
        help='The model will consider this day as "today" when generating data',
    )
    company_id = fields.Many2one("res.company")
    linked_action_ids = fields.One2many(
        "module.load.action", "demo_data_id", string="Linked Actions", readonly=True
    )
    # Modules to install data for booleans
    bool_generate_background = fields.Boolean(
        default=False, string="Generate Background Image"
    )
    bool_company_restricted = fields.Boolean(
        default=True,
        string="Product and contacts restricted to the company",
        help="Whether the generated data is shown only when the company is selected or not",
    )

    base = fields.Boolean(default=True, readonly=True)
    product = fields.Boolean(default=True, readonly=True)
    account = fields.Boolean(default=True, readonly=True)
    accountant = fields.Boolean(string="Accounting")
    crm = fields.Boolean(string="CRM")
    stock = fields.Boolean(string="Inventory")
    sale_management = fields.Boolean(string="Sales")
    purchase = fields.Boolean(string="Purchase")
    hr = fields.Boolean(string="Employees")
    website = fields.Boolean(string="Website")
    website_sale = fields.Boolean(string="Ecommerce")
    project = fields.Boolean(string="Project")
    timesheet_grid = fields.Boolean(string="Timesheets")
    mrp = fields.Boolean(string="Manufacturing")
    quality_control = fields.Boolean(string="Quality Control")
    mrp_plm = fields.Boolean(string="PLM")
    marketing_automation = fields.Boolean(string="Marketing Automation")
    hr_expense = fields.Boolean(string="Expenses")
    hr_recruitment = fields.Boolean(string="Recruitment")
    hr_holidays = fields.Boolean(string="Time off")
    helpdesk = fields.Boolean(string="Helpdesk")

    nb_employees = fields.Integer(
        string="Number of Employees (Approximately)", default=1
    )
    bool_gen_employees_pictures = fields.Boolean(
        string="Generate pictures for employees", default=False
    )

    percent_employees_pictures = fields.Float(
        string="Percentage of Pictures", default=0.1
    )

    _sql_constraints = [
        (
            "check_name_unique",
            "UNIQUE(name)",
            "The company name must be unique",
        ),
        (
            "check_percent_positive",
            "CHECK(percent_employees_pictures >= 0)",
            "The percentage must be positive",
        ),
        (
            "check_percent_inferior",
            "CHECK(percent_employees_pictures <= 1)",
            "The percentage cannot be over 100",
        ),
    ]

    @api.onchange("hr")
    def _onchange_hr(self):
        if not self.hr and self.nb_employees == 1:
            self.nb_employees = -1

    @api.constrains("nb_employees")
    def _check_nb_employees(self):
        for record in self:
            if record.nb_employees <= 0 and record.hr:
                raise ValidationError("Number of employees must be positive")

    mistral_model = fields.Char(
        help="To generate the data, we use the mistral-small model.",
        default="mistral-small-latest",
        readonly=True,
        string="Mistral Small",
    )

    # Informations supplémentaires sur l'entreprise:
    business_sector = fields.Char(
        string="Business Sector",
        default="",
        help="A description of the company's activity and services",
    )
    customer_individual = fields.Boolean(string="Individual", default=False)
    customer_professional = fields.Boolean(string="Professional", default=True)
    influence = fields.Char(
        default="", help="The area which the company covers with its services"
    )
    average_customer = fields.Char(
        string="Customer Types",
        default="",
        help="Which type of customers the company usually deals with",
    )

    is_company_created = fields.Boolean(default=False, copy=False, readonly=True)

    @api.depends("name")
    def _compute_data_path(self):
        print(self.name)
        path = os.path.join(
            os.path.dirname(tools.config.filestore(self._cr.dbname)),
            (self.name).lower().replace(" ", "_").strip(),
        )
        print(path)
        if not os.path.exists(path):
            os.mkdir(path)
        self.data_path = path

    data_path = fields.Char(
        readonly=True, copy=False
    )

    def _synthesize_info(self) -> str:
        business_string = (
            ("Secteur d'activité: " + self.business_sector)
            if self.business_sector
            else ""
        )
        customer_string = (
            "\n"
            + "Types de clients: "
            + (
                ("Clients particuliers" + " et " if self.customer_professional else "")
                if self.customer_individual
                else ""
            )
            + ("Clients professionels" if self.customer_professional else "")
        )
        influence_string = (
            ("\n" + "Rayonnement: " + self.influence) if self.influence else ""
        )
        average_customer_string = (
            ("\n" + "Profil des clients: " + self.average_customer)
            if self.average_customer
            else ""
        )
        return (
            business_string
            + customer_string
            + influence_string
            + average_customer_string
        )

    generation_in_progress = fields.Boolean(default=False)

    def _more_modules(self):
        """Creates the module actions when called by 'create_items()'
        Will only create the actions for which the dependent_module is checked and the dependent_var too (if exists)"""

        actions_to_load = [
            {
                "name": "General Information",
                "prompt": base_prompts.INFORMATION_PROMPT,
                "file_to_load": "general_information.txt",
                "dependent_module": "base",
            },
            {
                "name": "Background Image",
                "prompt": image_prompts.BACKGROUND_PROMPT,
                "file_to_load": "background_image.txt",
                "dependent_module": "base",
                "dependent_var": self.bool_generate_background,
            },
            {
                "name": "Products",
                "prompt": base_prompts.PRODUCTS_PROMPT,
                "file_to_load": "customer_products.csv;supplier_products.csv",
                "dependent_module": "account",
            },
            {
                "name": "Customers and Suppliers",
                "prompt": base_prompts.PARTNERS_PROMPT,
                "file_to_load": "customers.csv;suppliers.csv",
                "dependent_module": "account",
            },
            {
                "name": "Customer and Supplier bills",
                "prompt": base_prompts.BILLS_PROMPT,
                "file_to_load": "customer_bills.csv;supplier_bills.csv",
                "dependent_module": "account",
            },
            {
                "name": "CRM",
                "prompt": module_prompts.CRM_PROMPT,
                "file_to_load": "crm_leads.csv;crm_activities.csv",
                "dependent_module": "crm",
                "var": "\n- CRM Leads: The CRM leads must be realistic, and the partners should be the same as the previously created partners",
            },
            {
                "name": "Inventory",
                "prompt": module_prompts.INVENTORY_PROMPT,
                "file_to_load": "stock.csv",
                "dependent_module": "stock",
                "var": "\n- Inventory Adjustments: The products added to the inventory must be only physical goods, and the quantities of each product must be realistic for the company to have in stock",
            },
            {
                "name": "Sales",
                "prompt": module_prompts.SALES_PROMPT,
                "file_to_load": "sales.csv",
                "dependent_module": "sale_management",
                "var": "\n- Customer Sales: The customer sales must correspond to existing customer products and previously created customers ",
            },
            {
                "name": "Purchase",
                "prompt": module_prompts.PURCHASE_PROMPT,
                "file_to_load": "purchase.csv",
                "dependent_module": "purchase",
                "var": "\n- Supplier Purchases: The purchases must correspond to existing supplier products and previously created suppliers, and the transactions must be realistic—meaning the suppliers should logically sell these products, and the company should plausibly purchase them.",
            },
            {
                "name": "Employees",
                "prompt": module_prompts.HR_PROMPT,
                "file_to_load": "departments.csv;employees.csv",
                "dependent_module": "hr",
                "var": "\n Employees and Departments: The departments must be realistic for the industry and the employees organigram should be realistic",
            },
            {
                "name": "Employees Pictures",
                "dependent_module": "hr",
                "prompt": image_prompts.HR_PICTURES_PROMPT,
                "dependent_var": self.bool_gen_employees_pictures,
            },
        ]
        return_var = ""
        for action in actions_to_load:
            if "dependent_var" in action:
                if action["dependent_var"]:
                    del action["dependent_var"]
                else:
                    continue
            if self[action["dependent_module"]]:
                action["nb_tries"] = 0
                action["state"] = "in_line"
                action["demo_data_id"] = self.id
                if "var" in action:
                    return_var += action["var"]
                    del action["var"]
                self.env["module.load.action"].with_company(
                    self.env["res.company"]
                    .search([("name", "=", self.name)], limit=1)
                    .id
                ).create(action)
        return return_var

    @api.constrains("customer_individual", "customer_professional")
    def _check_customer_type(self):
        if not self.customer_individual and not self.customer_professional:
            raise ValidationError("There must be at least one customer type selected")

    module_list = [
        "accountant",
        "crm",
        "sale_management",
        "purchase",
        "stock",
        "hr",
        "website",
        "website_sale",
        "project",
        "timesheet_grid",
        "mrp",
        "quality_control",
        "mrp_plm",
        "marketing_automation",
        "hr_expense",
        "hr_recruitment",
        "hr_holidays",
        "helpdesk",
    ]

    def _install_modules(self):
        to_install = []
        for module in self.module_list:
            if self[module] and self.env["ir.module.module"].search(
                [("name", "=", module)], limit=1
            ).state in ["uninstalled", "to_install"]:
                to_install.append(module)
        self.env["ir.module.module"].search(
            [("name", "in", to_install)]
        ).button_immediate_install()

    def delete_all_items(self):
        self.is_active = False
        self.generation_in_progress = False
        for csv_file in Path(self.data_path).glob("*.csv"):
            csv_file.unlink()
        for txt_file in Path(self.data_path).glob("*.txt"):
            txt_file.unlink()
        for txt_file in Path(self.data_path).glob("*.jpg"):
            txt_file.unlink()
        _logger.warning("Removed all csv files from the database")
        if self.env["ir.module.module"].search(
            [("name", "=", "crm")], limit=1
        ).state in ["installed", "to_upgrade", "to_remove"]:
            self.env["crm.lead"].search([]).unlink()
        try:
            self.env["delivery.carrier"].search([]).unlink()
        except Exception:
            pass
        to_uninstall = []
        for module in self.module_list:
            if self.env["ir.module.module"].search(
                [("name", "=", module)], limit=1
            ).state in ["installed", "to_upgrade", "to_remove"]:
                to_uninstall.append(module)
        _logger.warning(f"Uninstalling modules: {to_uninstall}")
        self.env["ir.module.module"].search(
            [("name", "in", to_uninstall)]
        ).button_immediate_uninstall()
        try:
            self.env["sale.order.line"].search([]).product_uom_qty = 0
            for sale_order in self.env["sale.order"].search([]):
                sale_order.state = "cancel"
                sale_order.unlink()
        except Exception:
            pass
        account_move_list = self.env["account.move"].search([])
        for account_move in account_move_list:
            if account_move.state == "posted":
                account_move.button_draft()
        account_move_list.unlink()

        self.env["res.partner"].search(
            [
                "|",
                ("category_id", "like", "supplier"),
                ("category_id", "like", "customer"),
            ]
        ).unlink()
        self.env["product.combo.item"].search([]).unlink()
        self.env["product.template"].search([]).unlink()
        self.env["res.partner.category"].search([("name", "like", "customer")]).unlink()
        self.env["res.partner.category"].search([("name", "like", "supplier")]).unlink()
        self.env["module.load.action"].search([]).unlink()

    def create_company_open_wizard(self):
        return {
            "name": "Create Company Wizard",
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "demo.data.wizard",
            "target": "new",
            "context": {
                "default_name": self.name,
                "default_city": self.city,
                "default_country_id": self.country_id.id,
                "default_email": self.email,
                "default_logo": self.logo,
                "default_demo_data_id": self.id,
            },
        }

    def _get_file_value(self, file_name):
        try:
            with open(
                os.path.join(self.data_path, file_name), mode="r", encoding="utf-8-sig"
            ) as csv_file:
                return csv_file.read()
        except Exception:
            return ""

    def get_values_dict(self):
        def _stock_products_value():
            if (
                self.env["ir.module.module"]
                .search([("name", "=", "stock")], limit=1)
                .state
                in ["installed", "to_remove", "to_upgrade"]
                and self.stock
            ):
                return (
                    "Here are the pre-established quantities you have in stock for the products. Make sure the sold quantities are less than what is available in stock:\n```csv"
                    + self._get_file_value("stock.csv")
                    + "```"
                )
            else:
                return ""

        values_dict = {
            "__today__": self.demo_date.strftime("%Y-%m-%d"),
            "__global_prompt__": self._get_file_value("general_information.txt"),
            "__supplier_products__": self._get_file_value("supplier_products.csv"),
            "__customer_products__": self._get_file_value("customer_products.csv"),
            "__customers__": self._get_file_value("customers.csv"),
            "__suppliers__": self._get_file_value("suppliers.csv"),
            "__customer_bills__": self._get_file_value("customer_bills.csv"),
            "__supplier_bills__": self._get_file_value("supplier_bills.csv"),
            "__inventory__": _stock_products_value(),
            "__nb_employees__": str(self.nb_employees),
        }
        return values_dict

    def _generate_info(self, added_modules):
        language = "French" if self.lang == "fr_FR" else "English"
        new_message = (
            base_prompts.INFORMATION_PROMPT.replace("__name__", self.company_id.name)
            .replace("__city__", self.city)
            .replace("__today__", self.demo_date.strftime("%Y-%m-%d"))
            .replace("__country__", self.country_id.name)
            .replace("__language__", language)
            .replace("__company_information__", self._synthesize_info())
            .replace("__more_modules__", added_modules)
        )
        self.env["module.load.action"].search(
            [("name", "=", "General Information")], limit=1
        ).prompt = new_message

    def process_action_queue(self, number_items=3):
        _logger.warning("Executing Scheduled Action for data generation")
        if self.env["ir.cron"].search(
            [("name", "=", "CAP - Load Demo Data"), ("state", "=", "running")]
        ):
            _logger.error("ACTION CAP - LOAD DEMO DATA Already running")
            return False
        nb_done = 0
        for action in self.linked_action_ids:
            if nb_done == number_items or (
                action.state == "failed" and action.nb_tries >= 9
            ):
                break
            nb_done = (nb_done + 1) if action.exec_action() else nb_done
        if nb_done == number_items:
            _logger.warning(f"{number_items} actions executed successfully")
            return False
        _logger.warning(f"Could not execute {number_items} actions")
        return True

    def create_items(self):
        self.is_active = True
        self._install_modules()
        company_id = self.env["res.company"].search([("name", "=", self.name)], limit=1)
        if not company_id:
            raise UserError("There is no company created with the name %s" % self.name)
        self.company_id = company_id
        if not self.generation_in_progress:
            self.env["module.load.action"].search(
                [("demo_data_id", "=", self.id)]
            ).unlink()
            added_modules = self.with_company(company_id)._more_modules()

            self.with_company(company_id)._generate_info(added_modules)

        self.generation_in_progress = True
