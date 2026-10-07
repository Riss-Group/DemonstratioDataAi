INFORMATION_PROMPT = """
Objective: Gather detailed information about __name__ to create a comprehensive business description. This will guide LLM models in generating realistic Odoo-compatible CSV datasets for a commercial demo.

Available Company Data:
- Name: __name__
- City: __city__
- Country: __country__
- Additional Details: __company_information__


Research Requirements:
Use the given information to make a description fo the company, while making sure to expand on the following fields:

Industry: The company’s primary sector (e.g., tech, retail, manufacturing).
Products/Services:
- Customer Products: Typical offerings sold by the company (e.g., software, hardware, consulting).
- Supplier Products: Items/services the company procures (e.g., raw materials, SaaS tools).
Clients & Suppliers:
- Client Types: Target market (e.g., B2B, B2C, local/regional/international).
- Supplier Types: Common vendors (e.g., manufacturers, distributors, service providers).
Enhanced Business Description (To be generated)

Purpose: Enable LLM models to generate consistent, sector-specific data for:

Products (Customer/Supplier):
- Align with the company’s industry.
- Example: A tech company sells cloud software (customer products) and buys server hardware (supplier products).
Clients & Suppliers:
- Reflect real-world partnerships (e.g., retailers, wholesalers).
- Use real company names for suppliers where possible.
Invoices:
- Customer invoices > Supplier invoices (ensure profitability).
- Realistic amounts tied to product prices/quantities.

Constraints for Data Generation:

Language: __language__
Current Date: __today__
Data Recency: All generated records must be ≤ 1 month old.
**ALWAYS communicate these informations in your output prompt**


Instructions for Subsequent Models:
Context:
"Generate data for a name demo in Odoo. The company operates in [industry] and [key details from research]."
Products:
Customer Products: "[List typical offerings based on research]."
Supplier Products: "[List procured items/services]."
Partners:
Clients: "[Describe target market, e.g., SMEs in city]."
Suppliers: "[List realistic vendors, e.g., SupplierName for product]."
Invoices:
"Ensure total customer invoices exceed supplier invoices by ~20-30% to reflect profitability."
Realism:
Use actual product names/prices from the industry.
Avoid logical mismatches (e.g., a bakery buying microchips).
Output: Return only the enhanced business description and precise instructions for data generation. No additional commentary.
"""


PRODUCTS_PROMPT = """
You must generate two CSV files containing products that can be directly imported into Odoo's product.template model.

15 Customer Products with the following fields:
['name', 'default_code', 'list_price', 'type', 'description_sale']

15 Supplier Products with the same fields:
['name', 'default_code', 'list_price', 'type', 'description_sale']

Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are wrapped in quotes.
- Direct Import: The data must be importable directly into Odoo's product.template model.
- CSV Format: Both CSV files must start with ````csv` and end with `````.
- Business Consistency: Products must align with the company's industry as described in the global prompt. For example:
- If the company is in tech, products might include specific software, hardware, etc.
- Product Details: Descriptions should be detailed, reflecting typical features and benefits of products in this sector.
- Realistic Pricing: Product prices should be realistic and consistent with market standards for similar products.
- Product Type Field: The type field is a selection with two options:
    - 'consu' for Consumable Products
    - 'service' for Services Choose the appropriate type based on the product nature.

Company Description & Instructions:
__global_prompt__
"""

PARTNERS_PROMPT = """
You must generate two CSV files containing customers and suppliers that can be directly imported into Odoo's res.partner model.

Requirements:
15 Customers with the following fields:
['name', 'email', 'phone', 'mobile', 'street', 'city', 'zip', 'country', 'is_company']

15 Suppliers with the following fields:
['name', 'email', 'phone', 'mobile', 'street', 'city', 'zip', 'country', 'is_company', 'website', 'comment']

Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are wrapped in quotes.
- Direct Import: The data must be importable directly into Odoo's res.partner model.
- CSV Format: Both CSV files must start with ````csv` and end with `````.
Business Realism:
- The total amount of customer invoices must be greater than the total amount of supplier invoices (the company is profitable).
- Customers and suppliers must be realistically aligned with the products they would buy/sell, based on the following product lists.

Product Context:
Customer Products (what the company sells to customers):
```csv
__customer_products__
```


Supplier Products (what the company buys from suppliers):
```csv
__supplier_products__
```


Company Description & Instructions:
__global_prompt__
"""

BILLS_PROMPT = """
You must generate two CSV files containing customer and vendor invoices that can be directly imported into Odoo’s account.move model.

Requirements:
20 Customer Invoices with the following fields:
['partner_name', 'date', 'invoice_date_due', 'amount_untaxed', 'move_type', 'invoice_line_ids/quantity', 'invoice_line_ids/price_unit', 'invoice_line_ids/product_name']

10 Vendor Invoices with the same fields as above.

move_type must be 'in_invoice' for Vendor Invoices and 'out_invoices' for Customer Invoices

Additional Instructions:
- No Empty Fields: No field should be left blank. Ensure all text fields are wrapped in quotes.
- Direct Import: The data must be importable directly into Odoo’s account.move model.
- CSV Format: Both CSV files must start with ````csv` and end with `````.
- Use Existing Data: Use the products and partners defined in the previously generated CSV files.
- Invoiced products must match those listed in the product files.
- Partners must match those listed in the customer and supplier files.
- Transaction Consistency: Ensure invoiced products logically align with the partners. For example, a paper company should not invoice potatoes.
- Realistic Amounts: Invoice amounts should be realistic and consistent with product prices and invoiced quantities.


 The total of the customer invoices MUST BE SUPERIOR to the total of the vendor invoices


Invoice Dates:
- Dates should be realistic and spread across the last 3 months.
- At least 5 invoices (customer or vendor) must have due dates (invoice_date_due) after __today__.

Existing Data:
Customer Products:
```csv
__customer_products__
```


Supplier Products:
```csv
__supplier_products__
```


Customers:
```csv
__customers__
```


Suppliers:
```csv
__suppliers__
```

Company Description & Instructions:
__global_prompt__
"""
