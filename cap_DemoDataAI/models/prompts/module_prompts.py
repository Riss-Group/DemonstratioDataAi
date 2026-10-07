CRM_PROMPT = """
You must generate a CSV file containing CRM leads that can be directly imported into Odoo's crm.lead model.

Generate 10 leads for the crm.lead model with the following fields:
['name', 'expected_revenue', 'probability', 'partner_id', 'email_from', 'date_deadline', 'priority', 'stage_name']
- stage_name must be selected from: ['New', 'Qualified', 'Proposition', 'Won']
- priority must be a number between 0 and 3 (0: Low priority; 1: Medium Priority; 2: High Priority; 3: Very High priority)
- partner_id must match the names of existing partners from this list:
```csv
__customers__
```


Additionally, generate 8 activities for the mail.activity model linked to the previously generated CRM leads, with the following fields:
['activity_type_id', 'summary', 'date_deadline', 'lead_name']
- activity_type_id must be selected from: ['Email', 'To-do', 'Call', 'Meeting', 'Upload Document', 'Grant Approval']
- lead_name must match the name of the corresponding CRM lead.
- 6 of the date_deadline values must be after __today__, and 2 must be set to __today__.

Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are enclosed in quotes.
- Direct Importability: The data must be importable directly into Odoo’s crm.lead model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Leads must align with the company’s industry as described in the global prompt.
- Partner Names: The partner_id must match the names of existing partners.

Company Description & Instructions:
__global_prompt__
"""

SALES_PROMPT = """
You need to generate a CSV file containing Sales Orders that can be directly imported into Odoo's sale.order model.

Generate 20 sales orders for the sale.order model with the following fields:
['name', 'date_order', 'partner_id', 'amount_untaxed', 'order_line/quantity', 'order_line/price_unit', 'order_line/product_id']

- order_line/ refers to a single order line item.
- order_line/quantity is the purchased quantity of the product.
- order_line/price_unit is the unit price of the product.
- order_line/product_id must be one of the products listed below:
```csv
__customer_products__
```


partner_id must match the names of existing partners:
```csv
__customers__
```


Current stock for all products:
```csv
__inventory__
```

Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are wrapped in quotes.
- Direct Import: The data must be importable directly into Odoo’s sale.order model.
- CSV Format: The CSV must start with csv and end with .
- Business Consistency: Sales must align with the company’s operations and customers.
- Partner Names: Must match existing partner names exactly.
- Date: Sale dates must be after today (format: yyyy-mm-dd).

Company Description & Instructions:
__global_prompt__

"""


PURCHASE_PROMPT = """
You need to generate a CSV file containing Purchase Orders that can be directly imported into Odoo's purchase.order model.

Generate 20 purchase orders for the purchase.order model with the following fields:
['name', 'date_order', 'date_planned', 'partner_id', 'amount_untaxed', 'order_line/quantity', 'order_line/price_unit', 'order_line/product_id']

- date_planned refers to the product delivery date. It must be equal to or later than today (__today__).
- Dates should be generated in descending order (the 1st entry should be further in the future than the 20th).
- order_line/ refers to a single order line item.
- order_line/quantity is the purchased quantity of the product.
- order_line/price_unit is the unit price of the product.
- order_line/product_id must be one of the products listed below:
```csv
__supplier_products__
```


partner_id must match the names of existing suppliers:
```csv
__suppliers__
```


Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are wrapped in quotes.
- Direct Import: The data must be importable directly into Odoo’s purchase.order model.
- CSV Format: The CSV must start with csv and end with .
- Business Consistency: Purchases must align with the company’s industry and suppliers.
- Partner Names: Must match existing supplier names exactly.
Company Description & Instructions:
__global_prompt__

"""

INVENTORY_PROMPT = """
You must generate a CSV file containing Inventory Adjustments that can be directly imported into Odoo’s stock.quant model.

The CSV must include the following fields:
['product_id', 'inventory_quantity', 'inventory_date']

- product_id refers to the product name.
- inventory_quantity is the current recorded quantity of the product in the database.
- inventory_date is the next scheduled date for inventory verification.
- For each product with the type 'consu' (consumable) in the product lists below, add a corresponding line to the CSV.

Customer Products:
```csv
___customer_products__
```


Supplier Products:
```csv
__supplier_products__
```


Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are wrapped in quotes.
- Direct Import: The data must be importable directly into Odoo’s stock.quant model.
- CSV Format: The CSV must start with csv and end with .
- Product Names: Must match existing product names exactly.
- Consumable Products Only: Only products with type = 'consu' should be included in the inventory.
- Realistic Quantities: Product quantities should be fictional but realistic for this business (e.g., stock levels that make sense for their operations).

Company Description & Instructions:
__global_prompt__

"""

HR_PROMPT = """
Company Description & Instructions:
__global_prompt__


You must generate a CSV file containing HR Departments that can be directly imported into Odoo's hr.department model.

In this company, there are __nb_employees__ employees. Make sure to generate the correct number of employees and an accurate number of departments for this much employees
There usually are multiple people for each department.

Generate departments for the hr.department model with the following fields (as many as you believe correct for the size of this company):
['name']

Additionally, generate __nb_employees__ employees for the hr.employee model with the following fields:
['name', 'work_phone', 'work_email', 'job_title', 'department_id', 'parent_id', 'gender']
- department_id must be the name of a department previously generated
- parent_id is the name of this employee's manager, which must be an employee already generated. A manager can have multiple employees under him, and an employee may not be the manager of anyone. It is usual for this to happen in big companies as employees will work in teams rather than in a hierarchical order
- gender is a selection between 'male' and 'female'

Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are enclosed in quotes.
- Direct Importability: The data must be importable directly into Odoo’s hr model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Departments must align with the company’s industry as described in the global prompt.
- Manager Names: The manager must match the names of employee already generated. As such, you must generate the employees in decreasing order of authority.
- Do NOT Generate the same person twice, or a person with the same name as one already generated
"""
