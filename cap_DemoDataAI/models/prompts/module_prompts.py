CRM_PROMPT = """
Company Description & Instructions:
__global_prompt__

employees in the company:
```csv
__employees__
```

You need to generate a CSV file containing CRM tags that can be directly imported into Odoo's crm.tag model.
['name', 'color']
- 'name' must be a short descriptive name for the tag
- 'color' must be an integer between 1 and 11 representing the color of the tag in Odoo

You must generate a CSV file containing CRM leads that can be directly imported into Odoo's crm.lead model.

Generate 35 leads for the crm.lead model with the following fields:
['name', 'expected_revenue', 'probability', 'partner_id', 'email_from', 'date_deadline', 'priority', 'stage_name', 'user_id', 'tag_ids']
- stage_name must be selected from: ['New', 'Qualified', 'Proposition']
- user_id must be the name of an existing employee in the company, divide the leads evenly among the employees, all employees should have atleast 1 lead assigned to them
- tag_ids must be the name of one of the tags from the previously generated tags
- priority must be a number between 0 and 3 (0: Low priority; 1: Medium Priority; 2: High Priority; 3: Very High priority)
- partner_id must match the names of existing partners from this list:
```csv
__customers__
```


Additionally, generate 20 activities for the mail.activity model linked to the previously generated CRM leads, with the following fields:
['activity_type_id', 'summary', 'date_deadline', 'lead_name']
- activity_type_id must be selected from: ['Email', 'To-Do', 'Call', 'Meeting', 'Document']
- lead_name must match the name of the corresponding CRM lead.
- Atleast 5 of the activities must be overdue (date_deadline before __today__) and some must be upcoming (date_deadline after __today__).

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Assign 10 leads possible to the most senior employee (the one with the highest job title), but make sure all employees have at least 1 lead assigned to them.
- Direct Importability: The data must be importable directly into Odoo’s crm.lead model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Leads must align with the company’s industry as described in the global prompt.
- Partner Names: The partner_id must match the names of existing partners.
"""

SALES_PROMPT = """
Company Description & Instructions:
__global_prompt__


partner_id must match the names of existing partners:
```csv
__customers__
```

customer products that the company sells:
```csv
__customer_products__
```

Current stock for all products:
```csv
__inventory__
```


You need to generate two CSV file containing Sales Orders and sale order templates that can be directly imported into Odoo's.

Generate 20 sales orders for the sale.order model with the following fields:
['name', 'date_order', 'partner_id', 'amount_untaxed', 'order_line/quantity', 'price_unit', 'order_line/product_id']

- order_line/ refers to a single order line item. One order should only contain 1 product line
- order_line/quantity is the purchased quantity of the product.
- price_unit is the unit price of the product.
- order_line/product_id must be the name of one of the products listed above.



You also need to also generate 5 Template Quotations(sale.order.template) with following fields:
['name', 'order_line/quantity', 'order_line/product_id', 'terms_and_conditions', 'note']
- name must be a short descriptive name for the quotation template
- order_line/product_id must be the names of 3 products from the list of customer products shown above and should be separated by a semicolon (;).
- order_line/quantity is the quantity of the 3 products in the quotation template and should separated by a semicolon(;).
- terms_and_conditions must be a short description of the terms and conditions for this quotation template
- note must be a short note for this quotation template

- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
"""


PURCHASE_PROMPT = """
You need to generate a CSV file containing Purchase Orders that can be directly imported into Odoo's purchase.order model.

Generate 20 purchase orders for the purchase.order model with the following fields:
['name', 'date_order', 'date_planned', 'partner_id', 'amount_untaxed', 'order_line/name', 'order_line/quantity', 'order_line/price_unit', 'order_line/product_id']

- date_planned refers to the product delivery date. It must be equal to or later than today (__today__).
- Dates should be generated in descending order (the 1st entry should be further in the future than the 20th).
- order_line/ refers to a single order line item.
- order_line/name refers to the order line's name
- order_line/quantity is the purchased quantity of the product.
- order_line/price_unit is the unit price of the product.
- order_line/product_id must be the name of one of the products listed below:

```csv
__supplier_products__
```


partner_id must match the names of existing suppliers:
```csv
__suppliers__
```


Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Import: The data must be importable directly into Odoo’s purchase.order model.
- Business Consistency: Purchases must align with the company’s industry and suppliers.
- Partner Names: Must match existing supplier names exactly.
Company Description & Instructions:
__global_prompt__

"""

INVENTORY_PROMPT = """
You must generate two CSV files containing Inventory Adjustments (stock.quant) and Inventory Replenishment(stock.warehouse.orderpoint) that can be directly imported into Odoo.

Customer Products:
```csv
___customer_products__
```


Supplier Products:
```csv
__supplier_products__
```


Generate stock.quant for products which must include the following fields:
['product_id', 'inventory_quantity', 'inventory_date']

- product_id refers to the product name.
- inventory_quantity is the current recorded quantity of the product in the database.
- inventory_date is the next scheduled date for inventory verification.
- For each product with the type 'consu' (consumable) in the product lists below, add a corresponding line to the CSV.


Also you need to generate replenishment rules for 10 products which must include the following fields:
['product_id', 'min_qty', 'max_qty']
- product_id refers to the product name.
- min_qty is the minimum stock level that should trigger a replenishment.
- max_qty is the maximum stock level to be maintained after replenishment.

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Import: The data must be importable directly into Odoo’s stock.quant model.
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
['name', 'work_phone', 'work_email', 'job_title', 'department_id', 'parent_id', 'sex', 'hourly_cost']
- department_id must be the name of a department previously generated
- parent_id is the name of this employee's manager, which must be an employee already generated. A manager can have multiple employees under him, and an employee may not be the manager of anyone. It is usual for this to happen in big companies as employees will work in teams rather than in a hierarchical order
- sex is a selection between 'male' and 'female'
- hourly_cost is a float representing the cost of one hour of work for this employee. It must be a realistic number according to the employee's job title and the company's industry

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s hr model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Departments must align with the company’s industry as described in the global prompt.
- Manager Names: The manager must match the names of employee already generated. As such, you must generate the employees in decreasing order of authority.
- Do NOT Generate the same person twice, or a person with the same name as one already generated
"""


MRP_COMPONENTS_PROMPT = """
Company description and instructions:
__global_prompt__



YOU need to generate products and bills of material for the mrp (Manufacturing) module in Odoo in order to create demonstration data.
You have to generate manufacturing products on 2 levels:
You will be given products that are sold by the company. For the products which are 'goods' AND realistic to be produced by this company, you will create "component" products for these customer products (component products are the parts which can be assembled into the final product).
You MUST also generate products which will be used to produce those manufacturing components, meaning we will have manufacturing dependencies on two orders. Some of these components will be manufactured, and some will be purchasable.
As such, you will also create "Bill of Material" objects for the manufactured customer products but also for those manufactured component products, and the component products will be used as components in the Bill of Materials so they should not be the same as main product.
A BOM should NEVER consist the main product as one of the component product for which the BOM is made.
The main product should NEVER be added as a component to its own Bill of Materials.
There should be atleast two component in each Bill of Materials.

IMPORTANT RULE CHECK EXAMPLE:
Example of what NOT to do:
-If the Bill of Material is for "Cream", the component list must NOT include "Cream".

Example of correct data:
BOM for "Cream" → Components: Milk, Sugar, Flavor

You MUST verify before outputting that:
For every Bill of Material, none of its components have the same name as the Bill of Material product.
If a component name equals the BOM product name, REMOVE it.


Here are the customer products in csv format, in the same format in which they are imported into the database:
```csv
__customer_products__
```

Generate 20 component products for the product.template Odoo model with the following fields. Follow the rules set above.
['name', 'default_code', 'list_price', 'route_ids', 'purchase_ok']
- 'route_ids' is either "Buy" if the product has to be purchased or "Manufacture" if we are the ones to manufacture it 
- 'purchase_ok' is a python Boolean: True if this product is purchasable, False if not


Generate a CSV file for the mrp.bom Odoo model a Bill of Material object for each of the customer products that can be manufactured, and for each of the manufacturing components that can be manufactured 

The Bills of Materials you will generate must have the following fields:
['product_tmpl_id', 'product_qty', 'code']
- 'code' must correspond to an unique code for this Bill of Materials
- 'product_tmpl_id' must correspond to the name of the manufactured product
- 'product_qty': the quantity of the manufactured product that will be produced with this bill of materials

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Do NOT create Bills of Materials for the products that are bought. Only for those that need to be manufactured
- Do NOT create Bills of Materials for products that have no realistic manufacturing components
- You must create AT MOST 10 Bills of material for customer products. The rest must be for the components
- Make sure the products used in the BOM don't make a cycle like A needs B, B needs C, C needs A
- No Empty Fields: No field should be left blank
"""

MRP_BOM_PROMPT = """
You have to generate data for the mrp (Manufacturing) module in Odoo for a commercial demonstration.
Given the following component products and Bills of Material, you have to assign to each Bill of Materials its component products. 
The main product should NEVER be added as a component to its own Bill of Materials.
A BOM should NEVER consist the main product as one of the component product for which the BOM is made.

IMPORTANT RULE CHECK EXAMPLE:
Example of what NOT to do:
-If the Bill of Material is for "Cream", the component list must NOT include "Cream".

Example of correct data:
BOM for "Cream" → Components: Milk, Sugar, Flavor

You MUST verify before outputting that:
For every Bill of Material, none of its components have the same name as the Bill of Material product.
If a component name equals the BOM product name, REMOVE it.

The CSV containing all Bills of Materials:
```csv
__bom__
```

The CSV containing all the component products:
```csv
__component_products__
```

Generate a CSV file for the mrp.bom.line Odoo model
The main product should NEVER be added as a component to its own Bill of Materials.
For each component needed in each of these Bills of Material, generate a component line which tells how much a certain product is needed for the manufacture in each bill of material.
-Such as if the main product is a "Cream", the components could be "Milk", "Sugar", "Flavor" and "Cream" itself should NOT be the component.
Generate this CSV with the following fields:
['product_id', 'product_qty', 'code']
- 'product_id' refers to the name of the product used in the fabrication
- 'product_qty' must be the quantity of said product_id needed in the manufacturing of the bill of material
- 'code' must be the corresponding Bill of Material's code

Instructions:
-Such as if the main product is a "Cream", the components could be "Milk", "Sugar", "Flavor" and "Cream" itself should NOT be the component.
- A product CANNOT require itself as a component in a Bill of Materials
- A Manufactured product MUST have components in its Bill of Materials
- Each Bill of Material must have multiple products as components.
- All products in the component products CSV must be used in a Bill of Material as components
- The components for a product have to be realistic.
- Make sure the products used in the BOM don't make a cycle like A needs B, B needs C, C needs A
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
ALL CSV FILES HAVE TO BE CORRECTLY FORMATTED. THERE MUST NOT BE ANY USELESS LINE BREAK IN THE CSV FILES.
"""

WORK_CENTERS_PROMPT = """
Global instructions:
__global_prompt__



You are to generate a CSV that will contain Work Centers for an Odoo demonstration database.

Given Bills of Materials already generated and their related generated components, You will first generate work centers, and you will associate those work centers to the corresponding Bills of Materials

Here are the Bills of Materials pre-generated:
```csv
__bom__
```

Here are the components, with the field 'manufactured_product' being the corresponding Bill of Material's manufactured product.
```csv
__bom_components__
```

Generate a CSV file with realistic work centers for these Bills of Materials and realistic for the company producing the products.
The CSV file must have the following fields:
['name', 'code', 'time_efficiency', 'costs_hour']
- 'code' is the code set for the work center
- 'time_efficiency' is a float between 0 and 1
- 'costs_hour' is the money it costs for an hour of production at this work center. It MUST BE a float
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
"""

WORK_ORDERS_PROMPT = """

Global Instructions:

__global_prompt__


You have to generate Work orders for Bills of Materials in the mrp (manufacturing) module in Odoo

You will be given pre-generated Work Centers and Bills of Materials, and you must generate Work Orders to assign to these Bills of Materials

Here is the CSV for the Bills of Materials:
```csv
__bom__
```

Here is the CSV for the Work Centers:
```csv
__work_centers__
```

Generate a CSV file with Work Orders (mrp.routing.workcenter) for each of the Bills of Materials. Those work orders must be realistic for the bills of materials.
The number should be 3 operations per Bill of Materials.

The generated work orders must have the following fields:
['name', 'workcenter_id', 'time_cycle_manual', 'employee_ratio', 'bom_id']
- 'workcenter_id' must be the code of the associated Work Center
- 'time_cycle_manual': time used to complete the order
- 'employee_ratio': number of employees needed for the order
- 'bom_id': Code of the corresponding Bill of Materials

- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
"""

MANUFACTURING_ORDERS_PROMPT = """
Global Instructions:

__global_prompt__


Given a list of Bills of Materials from the mrp (Manufacturing) module in Odoo, you have to generate manufacturing orders for a demonstration database.

you will be given two CSV files:
One containing Bills of Materials (from the mrp.bom model in Odoo)
The other containing the components needed for each of these Bills of Materials (from the mrp.bom.line model in Odoo)
You have to generate a CSV file containing manufacturing orders for these Bills of Materials

Bills of Materials CSV file:
```csv
__bom__
```

Bills of Materials Lines CSV file:
```csv
__bom_components__
```

For each Bill of Material, generate one corresponding Manufacturing Order (mrp.production model) that is either already produced or to produce:
The CSV file must have the following fields:
['product_id', 'product_qty', 'state', 'date_start']
- 'product_id' is the name of the product in the Bill of Materials
- 'product_id' is the quantity of this product you want to create with this Manufacturing Order
- 'state' is a selection between 'confirmed', 'in_progress', 'done'. There should be orders in all states.

Additional Instructions
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s mrp.production model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
"""


BOM_OPERATION_INSTRUCTIONS = """
Global Instructions: 

__global_prompt__ 

You are given two CSV files extracted from Odoo:

- Bills of Materials (mrp.bom) — contains product and routing data for manufactured products.
- Work Orders (mrp.routing.workcenter) — contains the operations associated with each Bill of Materials.

Your task is to generate a CSV file containing work order instructions (quality.point) for only the first Bill of Materials found in the provided BOM CSV.


- Identify the first BOM in the
```csv
__bom__
```
- Extract its product name (from the `product_tmpl_id` column).

- Find all work orders in the following csv that belong to this BOM.
```csv
__work_orders__
```

- A column like `bom_id` name match exists, use that to filter.

Generate one instruction per work order.

The output must have exactly one row per work order for the selected BOM, and exactly these columns:
['name', 'product_ids', 'note']

Rules:
- 'name' should be the exact name of the work order to which the instruction applies.
- 'product_ids' must contain the product name from the BOM.
- 'note' a descriptive instruction.

---

- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Each `name` and `note` must be unique per work order.
- Use realistic manufacturing or quality control instructions relevant to each operation name.
- CSV Format: CSV file must start with ```csv and end with ```.
- Do not generate instructions for more than one BOM.
"""

PROJECT_AND_TASKS_PROMPT = """
Global Instructions:

__global_prompt__


Employees in the company:
```csv
__employees__
```

Customers in the company:
```csv
__customers__
```

You need to generate two CSV file containing Project(project.project) and Tasks(project.task) that can be directly imported in odoo.
Generate 6 projects for the project.project model with the following fields:
['name', 'user_id', 'privacy_visibility','partner_id']

- 'user_id' must be the name of an existing employee in the company, divide the projects evenly among the employees
- 'privacy_visibility' must be one of the following: 'followers', 'employees', 'invited_users', 'portal'
- 'partner_id' must be the name of an existing customer in the company from the list above

You also need to generate 30 tasks for the project.task model with the following fields:
['name', 'user_ids', 'description', 'project_id', 'planned_date_begin', 'date_deadline', 'priority', 'stage_id']
- 'user_ids' must be the name of an existing employee in the company, only one employee should be assigned per task
- 'description' must be a short description of the task
- 'project_id' must be the name of a project previously generated
- 'date_deadline' must be a date after __today__
- The planned_date_begin must be before the date_deadline
- 'planned_date_begin' must be a date before __today__
- 'priority' must be a number between 0 and 3 (0: Low priority; 1: Medium Priority; 2: High Priority; 3: Very High priority)
- 'stage_id' must be one of the following: 'New', 'In Progress', 'Blocked', 'Ready for Review', 'Done'

Additionally you need to generate timesheets for these tasks, with the following fields:
['name','employee_id','unit_amount','date','task_id', 'project_id']
- 'name' must be a 3 to 5 line description of the work done, it should be relevant to the task and descriptive
- 'employee_id' must be the name of an existing employee in the company
- 'unit_amount' must be a number between 0.5 and 8 (representing hours worked)
- 'date' must be a date before __today__
- 'task_id' must be the name of a task previously generated
- 'project_id' must be the name of the project associated with the task
- Make sure that one employee does not log more than 8 hours of work on the same day across all tasks


Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- The planned_date_begin must NEVER be after the date_deadline
- All tasks should be evenly distributed among the projects.
- Direct Importability: The data must be importable directly into Odoo’s project.project and project.task models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Projects and tasks must align with the company’s industry as described in the global prompt.
- User Names: The user_id must match the names of existing employees.
"""

HELPDESK_PROMPT = """
Global Instructions:
__global_prompt__

Customers and Suppliers in the company:
```csv
__customers__
__suppliers__
```

Employees in the company:
```csv
__employees__
```

You need to generate three CSV files containing Helpdesk Teams (helpdesk.team) , Helpdesk Tickets (helpdesk.ticket) and Timesheets (account.analytic.line) 
that can be directly imported into Odoo.

Generate 3 helpdesk teams for the helpdesk.team model with the following fields:
['name', 'description', 'member_ids']
- 'member_ids' must be the names of existing employees in the company, divide the employees evenly among the teams
- the member names must be in the same row separated by a semicolon (;)
- the members should not be repeated among teams
- 'description' must be a short description for the team


Generate 15 helpdesk tickets for the helpdesk.ticket model with the following fields:
['name', 'description', 'partner_id', 'user_id', 'team_id', 'stage_id']
- 'partner_id' must be the name of an existing customer/supplier in the company from the list above and must never be empty
- 'team_id' must be one of the above created helpdesk teams
- 'user_id' must be the name of an employee from the assigned team
- 'stage_id' must be one of the following: 'New', 'In Progress', 'On Hold', 'Solved', 'Cancelled'

Generate timesheets for these tickets, with the following fields:
['name','unit_amount','date' ,'helpdesk_ticket_id']
- 'helpdesk_ticket_id' must be the name of a ticket previously generated
- 'name' must be a 3 to 5 line description of the work done, it should be relevant to the ticket and descriptive
- 'unit_amount' must be a number between 0.5 and 8 (representing hours worked)
- 'date' must be a date before __today__
- There should be at least one timesheet per ticket

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- 'description' and 'member_ids' field should be in double quotes ("TEXT").
- All tickets should be evenly distributed among the teams.
- Direct Importability: The data must be importable directly into Odoo’s project.project and project.task models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Teams and tickets must align with the company’s industry as described in the global prompt.
"""

EVENTS_PROMPT = """
Global Instructions:
__global_prompt__       


Employees in the company:
```csv
__employees__
```

Customers in the company:
```csv
__customers__
```

You need to generate two CSV files containing Event Venues and Events (event.event) that can be directly imported into Odoo.

Generate 10 Venues with the following fields:
['name', 'email', 'phone', 'street', 'city', 'zip', 'country']


Generate 6 events for the event.event model with the following fields:
['name', 'date_begin', 'date_end', 'address_id', 'user_id', 'organizer_id', 'stage_id']
- 'stage_id' must be one of the following: ['New', 'Booked', 'Announced', 'Ended']
- Assign the stage_id according to the dates of the event
- 'date_begin' must be a date after __today__
- 'date_end' must be a date after 'date_begin'
- Make it so that some events are ongoing and not all in the future
- 'user_id' must be the name of an existing employee in the company
- 'organizer_id' must be the name of an existing partner company in the company contacts
- 'address_id' must be the name of one of the previously generated venues

You need to generate CSV files containing Event Tickets (event.event.ticket) that can be directly imported into Odoo.
Generate event tickets for the event.event.ticket model with the following fields:
['name', 'price', 'seats_max', 'start_sale_datetime', 'end_sale_datetime', 'event_id']
- 'event_id' must be the name of a event previously generated
- 'name' must be one of the following: 'VIP', 'Standard', 'Free'
- 'price' must be a float number greater than or equal to 0. 'Free' tickets must have a price of 0
- 'seats_max' must be an integer greater than 0
- 'start_sale_datetime' must be a date
- 'end_sale_datetime' must be a date after 'start_sale_datetime'
- The sale dates must be set according to the event dates
- All events must have all three types of tickets


Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s event.event and event.registration models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Events must align with the company’s industry as described in the global prompt.
"""

EVENT_TICKETS_REGISTRATION_PROMPT = """
Global Instructions:
__global_prompt__

Events in the company:
```csv
__events__
```

Customers in the company:
```csv
__child_contacts__
```

You need to generate CSV files containing Event Registrations (event.registration) that can be directly imported into Odoo.

Generate 25 event registrations for the event.registration model with the following fields:
['event_id', 'event_ticket_id','partner_id]
- 'event_id' must be the name of an existing event from the list above
- 'event_ticket_id' must be one from ['VIP', 'Standard', 'Free']
- 'partner_id' must be the name of an existing customer  in the company from the list above
- Each registered individual should only attend one event
- The number of registrations should be divided equally among the events
- All events should have atleast one type of each ticket (event_ticket_id) registered

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s event.event.ticket and event.registration models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Tickets and registrations must align with the company’s industry as described in the global prompt.
"""


MARKETING_AUTOMATION_PROMPT = """
Global Instructions:
__global_prompt__

Generate 20 mailing contact for marketing campaigns for the mailing.contact model with the following fields:
['name', 'email', 'company_name', 'country_id']
- 'email' must be unique for each contact
- 'country_id' must be a valid country name
- 'company_name' must be a realistic company name

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s mailing.contact model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
"""

KNOWLEDGE_ARTICLE_PROMPT = """
Global Instructions:
__global_prompt__

You need to generate a CSV file containing Knowledge Articles that can be directly imported into Odoo's knowledge.article model.


Generate 4 knowledge articles for the knowledge.article model one for each of the articles as :
- Sales process
- Supply chain process
- HR Process
- Administrative process

With the following fields:
['name', 'title', 'intro', 'body', 'conclusion']
- 'name' must be one of the ['Sales process', 'Supply chain process', 'HR Process', 'Administrative process']
- 'title' must be a concise title for the article
- 'intro' must be a brief introduction summarizing the article's content
- 'body' must contain at least 500 words of relevant content related to the company's industry, 
it should provide detailed information, insights, and explanations about the topic and be well-structured with headings 
and subheadings for clarity with valid html formatting
- 'conclusion' must summarize the key points discussed in the article

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- The csv should have proper formatting of data.
- Direct Importability: The data must be importable directly into Odoo’s knowledge.article model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Articles must align with the company’s industry as described in the global prompt.
"""

TIME_OFF_REQUESTS_PROMPT = """
Global Instructions:
__global_prompt__

Employees in the company:
```csv
__employees__
```

You need to generate a CSV file containing Public Holidays that can be directly imported into Odoo's resource.calendar.leaves model.

Generate ALL the public holidays for the resource.calendar.leaves model for the current year(__today__ year of this date[from 1 jan to 31 dec for this year]) of the country __country__ with the following fields:
['name', 'date_from', 'date_to']
- 'name' must be the name of the public holiday
- 'date_from' must be the starting date of the public holiday
- 'date_to' must be the ending date of the public holiday
- Two public holidays cannot overlap each other, strictly follow this rule 
- Mandate days should only be created for the current year


Generate 5 mandate days for the hr.leave.mandatory.day model for the current year(__today__ year of this date[from 1 jan to 31 dec for this year]) of the country __country__ with the following fields:
['name', 'date']
- 'name' must be the name of the mandate day
- 'date' must be the date of the mandate day
- Two mandate days cannot overlap each other
- At least 2 mandate should be after __today__
- Mandate days cannot overlap with public holidays

You need to generate 2 CSV file containing Time Off Allocation (hr.leave.allocation) and Time Off Requests (hr.leave) that can be directly imported into Odoo.

Generate time off Allocation for each of these types ['Paid Time Off', 'Extra Time Off', 'Compensatory Days'] for all employees for the hr.leave.allocation model with the following fields:
['notes', 'employee_id', 'holiday_status_id', 'number_of_days']
- 'notes' must be a short descriptive name for the time off allocation
- 'employee_id' must be the name of an existing employee in the company
- 'holiday_status_id' must be one of the following: ['Paid Time Off', 'Extra Time Off', 'Compensatory Days']
- 'number_of_days' must be an integer between 5 and 30
- All employees must have an allocation for each of the above types


Generate 30 time off requests for the hr.leave model with the following fields:
['name', 'employee_id', 'holiday_status_id', 'request_date_from', 'request_date_to']
- 'name' a short, descriptive title for the leave (e.g., “Medical Appointment”, “Annual Vacation”).
- 'employee_id' must reference the name of an existing employee in the company.
- 'holiday_status_id' Must be one of the following leave types:
 ['Paid Time Off', 'Sick Time Off', 'Unpaid', 'Extra Time Off', 'Compensatory Days']
- Leaves should only be created according to the allocated leave types for each employee(if a employee has an allocation for 'Paid Time Off' for 10 days, then he should not have more than 10 days of 'Paid Time Off' leaves requested)
- 'request_date_from' must be a valid date within the current calendar year (__today__ year, from Jan 1 to Dec 31). Some leaves must be before today and some after today.
- 'request_date_to' must be strictly after request_date_from. Must fall within the same calendar year.
- Working Days Only Leave dates must not include or overlap: Weekends (Saturday or Sunday), Public Holidays, Mandated days.
- Single-Day Leaves: Must fall entirely on a valid working day, Must not coincide with weekends(Saturdays or Sundays), public holidays, or mandated days
- Multi-Day Leaves: Must start and end on working days
- No Overlapping Leaves: A single employee must not have overlapping leave dates, regardless of leave type
- Leave Type Uniqueness per Employee: Each employee must have at most one leave request per leave type.
- Each employee must have at least one leave request for every leave type listed in holiday_status_id, This ensures complete coverage of all leave allocations for each employee within the year,
If multiple employees exist, every employee must independently satisfy this requirement
- Ensure a realistic mix of: Single-day and multi-day leaves, Past and future leaves (relative to today)
- Leave names and durations should be contextually appropriate to the leave type,e.g., Sick leaves are typically short, PTO may be longer, Compensatory Days are often single-day

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Working Days Only Leave dates must not include or overlap:Weekends (Saturday or Sunday), Public Holidays, Mandated days.
- Single-Day Leaves: Must fall entirely on a valid working day, Must not coincide with weekends(Saturdays or Sundays), public holidays, or mandated days
- Multi-Day Leaves: Must start and end on working days
- There should be atleast one leave request for the current month
- No employee should have overlapping leaves on the same dates(e.g. an employee has a leave from 1st to 5th Jan, he cannot have another leave from 4th to 6th Jan)
- Direct Importability: The data must be importable directly into Odoo’s hr.leave model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Time off requests must align with the company’s industry as described in the global prompt.
- Employee Names: The employee_id must match the names of existing employees.
"""

RECRUITMENT_PROMPT = """
Global Instructions:
__global_prompt__

Employees in the company:
```csv
__employees__
```

Departments in the company:
```csv
__departments__
```

You need to generate two CSV files containing Job Positions (hr.job) and Job Applications (hr.applicant) that can be directly imported into Odoo.

Generate 10 job positions for the hr.job model with the following fields:
['name', 'department_id', 'description', 'contract_type_id', 'user_id', 'expected_degree', 'interviewer_ids', 'no_of_recruitment']
- 'name' must be a realistic job title relevant to the company's industry
- 'department_id' must be a department name from the above list
- 'description' must be a brief description of the job responsibilities
- 'contract_type_id' must be one of the following: ['Full-Time', 'Part-time', 'Intern', 'Permanent', 'Temporary', 'Interim', 'Seasonal', 'Thesis', 'Statutory', 'Employee']
- 'user_id' must be the name of an existing employee in the company, ideally someone from the HR department
- 'expected_degree' must be one of the following: ['Bachelor Degree', 'Master Degree', 'Doctoral Degree', 'Graduate']
- 'interviewer_ids' must be the names of existing employees in the company, ideally from the HR department, separated by semicolons (;)
- 'no_of_recruitment' must be an integer between 1 and 10

Additionally, generate 50 job applications for the hr.applicant model with the following fields:
['partner_name', 'email_from', 'partner_phone', 'job_id', 'stage_id', 'user_id', 'type_id', 'availability', 'applicant_notes', 'salary_expected', 'salary_proposed', 'priority']
- 'partner_name' must be a realistic full name
- 'email_from' must be a realistic email address
- 'partner_phone' must be a realistic phone number
- 'job_id' must be the name of a job position previously generated
- 'stage_id' must be one of the following: ['New', 'Qualification', 'First Interview', 'Second Interview', 'Contract Proposal', 'Contract Signed']
- 'user_id' must be the name of an existing employee in the company
- 'type_id' must be one of the following: ['Graduate', 'Bachelor Degree', 'Master Degree', 'Doctoral Degree']
- 'availability' must be a date after __today__
- 'applicant_notes' must be a brief note about the applicant's qualifications or experience
- 'salary_expected' must be a realistic salary expectation based on the job position
- 'salary_proposed' must be a realistic salary proposal based on the job position and the applicant's qualifications
- 'priority' must be a number between 0 and 3 (0: Low priority; 1: Medium Priority; 2: High Priority; 3: Very High priority)

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s hr.job and hr.applicant models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Job positions and applications must align with the company’s industry as described in the global prompt.
- Employee Names: The responsible_id must match the names of existing employees.
- For the salary_expected and salary_proposed, make sure to only fill the number without any currency symbol
"""

POS_PRODUCTS_PROMPT = """
Global Instructions:
__global_prompt__

You need to generate two CSV files containing Products for the POS (product.template) and combo products for POS (product.combo) that can be directly imported into Odoo.

Generate 15 products for POS for the product.template model with the following fields:
['name', 'default_code', 'list_price', 'category_id']
- The products should only be component products that can be sold in a POS environment
- 'name' must be a realistic product name relevant to the company's industry
- 'default_code' must be a unique product code
- 'list_price' must be a realistic price for the product
- 'category_id' must be a category relevant to the product


Generate 5 combo products for POS for the product.combo model with the following fields:
['name', 'default_code', 'list_price', 'products']
- 'products' must be atleast two product names from the previously generated POS products, separated by semicolons (;)
- 'name' must be a realistic combo product name relevant to the company's industry
- 'default_code' must be a unique product code
- 'list_price' must be a realistic price for the combo product


Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s product.template and product.combo models 
- The products should only be component products that can be sold in a POS environment
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Products must align with the company’s industry as described in the global prompt.
"""

POS_PROMPT = """
Global Instructions:
__global_prompt__

Employees in the company:
```csv
__employees__
```

Customers in the company:
```csv
__customers__
```

Products for the POS:
```csv
__pos_products__
```

You need to generate two CSV files containing POS Sessions (pos.session) and POS Orders (pos.order) that can be directly imported into Odoo.

Generate 5 POS sessions for the pos.session model with the following fields:
['name', 'config_id', 'start_at', 'stop_at', 'state']
- 'name' must be a realistic session name
- 'config_id' must be one of the following: ['Main POS', 'Bar POS', 'Restaurant POS', 'Retail POS']
- 'start_at' must be a date before __today__
- 'stop_at' must be a date after 'start_at'
- 'state' must be one of the following: ['opened', 'opening_control', 'closed', 'closing_control']
- All sessions must be before __today__ date

Additionally, generate 15 POS orders for the pos.order model with the following fields:
['name', 'session_id', 'date_order', 'partner_id', 'product_id']
- 'session_id' must be the name of a POS session previously generated
- 'date_order' must be a date between the 'start_at' and 'stop_at' of the associated POS session
- 'partner_id' must be the name of an existing customer in the company
- 'product_id' must be the name of an existing POS product previously generated

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s pos.session and pos.order models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: POS sessions and orders must align with the company’s industry as described in the global prompt.
"""

FIELD_SERVICE_PROMPT = """
Global Instructions:
__global_prompt__   

Employees in the company:
```csv
__employees__
```

Customers in the company:
```csv
__customers__
```

You need to generate two CSV files containing Field Service Tasks (project.task) and Field Service Task Timesheet (account.analytic.line) that can be directly imported into Odoo.

Generate 20 field service tasks for the project.task model with the following fields:
['name', 'partner_id', 'user_id', 'description', 'planned_date_begin', 'date_deadline', 'priority', 'stage_id']
- 'name' must be a realistic task name relevant to the company's industry
- 'partner_id' must be the name of an existing customer in the company
- 'user_id' must be the name of an existing employee in the company, divide the tasks evenly among all the employees
- 'description' must be a brief description of the task
- 'date_deadline' must be a date after __today__
- The planned_date_begin must be before the date_deadline
- 'planned_date_begin' must be a date before __today__
- 'priority' must be a number between 0 and 3 (0: Low priority; 1: Medium Priority; 2: High Priority; 3: Very High priority)
- 'stage_id' must be one of the following: ['New', 'In Progress', 'Planned', 'Cancelled', 'Done']


You also need to generate timesheets for these tasks, with the following fields:
['name','employee_id','unit_amount','date','task_id']
- 'name' must be a 3 to 5 line description of the work done, it should be relevant to the task and descriptive
- 'employee_id' must be the name of an existing employee in the company
- 'unit_amount' must be a number between 0.5 and 8 (representing hours worked)
- 'date' must be a date before __today__
- 'task_id' must be the name of a task previously generated

Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s project.task models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Field service tasks must align with the company’s industry as described in the global prompt.
"""

FIELD_SERVICE_WORKSHEET_PROMPT = """
Global Instructions:
__global_prompt__

You are given two CSV files extracted from Odoo:
- Field Service Tasks (project.task) — contains task data for field service operations.
- Field Service Task Timesheet (account.analytic.line) — contains timesheet entries associated with each field service task.

Your task is to generate a CSV file containing field service worksheets (fieldservice.worksheet) for tasks found in the provided tasks CSV.
```csv
__fsm_tasks__
```

- Extract their names (from the `name` column).
- Find all timesheet entries in the following csv that belong to these tasks.
```csv
__fsm_timesheets__
```

- A column like `task_id` name match exists, use that to filter.
Generate one worksheet per task.
The output must have exactly one row per task for the selected tasks, and exactly these columns:
['service_category', 'task_id', 'work_description', 'work_performed', 'equipment_details', 'condition_before_service', 'condition_after_service', 'date', 'time_spent']
- 'service_category' should be one from ['installation','maintenance','repair','inspection','support','upgrade'].
- 'work_description' must contain the description of the work to be performed for the task, it should be relevant to the task and descriptive
- 'work_performed' a descriptive summary of the work done based on the timesheet entries for that task.
- 'equipment_details' should contain details of the equipment used for the task, it should be relevant to the task and descriptive
- 'condition_before_service' should contain a description of the condition before the service was performed, it should be relevant to the task and descriptive
- 'condition_after_service' should contain a description of the condition after the service was performed, it should be relevant to the task and descriptive
- 'date' should be the date of the most recent timesheet entry for that task
- 'time_spent' should be the total time spent on the task based on the timesheet entries for that task


Additional Instructions:
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT. DO NOT PUT MODEL NAME INSIDE CSV CONTENT.
- Direct Importability: The data must be importable directly into Odoo’s worksheet template models.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Field service tasks must align with the company’s industry as described in the global prompt.
"""
