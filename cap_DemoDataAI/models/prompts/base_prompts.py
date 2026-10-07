
INFORMATION_PROMPT_1 = """
Objective: Gather detailed information about __name__ to create a comprehensive business description. This will guide LLM models in generating realistic Odoo-compatible CSV datasets for a commercial demo.

Available Company Data:
- Name: __name__
- City: __city__
- Website: __website__
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
All date fields must be formatted as '%Y-%m-%d'.
**ALWAYS communicate these information's in your output prompt**
All character fields whose value is in sentence(more than a single word) form should be in double quotes("TEXT")


Instructions for Subsequent Models:
Context:
"Generate data for a name demo in Odoo. The company operates in [industry] and [key details from research]."
Invoices:
"Ensure total customer invoices exceed supplier invoices by ~20-30% to reflect profitability."
Realism:
Use actual product names/prices from the industry.
Avoid logical mismatches (e.g., a bakery buying microchips).
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- All money fields should only contain numeric values (e.g., 99.99) and not any symbols.
- Make sure to strictly only use the fields mentioned in each prompt for data generation.
- Strictly follow the CSV format mentioned in each prompt for data generation.
- Field data should be Strictly of the type mentioned in each prompt for data generation.
Output: Return only the enhanced business description and precise instructions for data generation. No additional commentary.
"""


INFORMATION_PROMPT_2 = """
Objective: Gather detailed information about __name__ to create a comprehensive business description. This will guide LLM models in generating realistic Odoo-compatible CSV datasets for a commercial demo.

Search the following Company Data from the web using the name and website provided:
- Name: __name__
- City: [Get the city where the company is located]
- Country: __country__
- Website: __website__
- Additional Details: [Business sector: industry the company operates in],
[Customer Types: Individuals, SMEs, large enterprises],
[Market Influence: International presence, local dominance],
[Customer profile: type of customers usually served],

In the response include a dictionary with the following fields filled out based on your research( Never Forget to add this dictionary in your response):
```json
{"Business sector": "",
"City": "",
"Customer Types": "",
"Market Influence": "",
"Customer profile": ""
}
```

It should be warped the way above.

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
All date fields must be formatted as '%Y-%m-%d'.
**ALWAYS communicate these information's in your output prompt**
All character fields whose value is in sentence(more than a single word) form should be in double quotes("TEXT")


Instructions for Subsequent Models:
Context:
"Generate data for a name demo in Odoo. The company operates in [industry] and [key details from research]."
Invoices:
"Ensure total customer invoices exceed supplier invoices by ~20-30% to reflect profitability."
Realism:
Use actual product names/prices from the industry.
Avoid logical mismatches (e.g., a bakery buying microchips).
- No Empty Fields: No field should be left blank. DO NOT MAKE EXTRA FIELDS.
- All fields should be in double quotes to handle commas within the text("TEXT").
- All money fields should only contain numeric values (e.g., 99.99) and not any symbols.
- Make sure to strictly only use the fields mentioned in each prompt for data generation.
- Strictly follow the CSV format mentioned in each prompt for data generation.
- Field data should be Strictly of the type mentioned in each prompt for data generation.
Output: Return only the enhanced business description and precise instructions for data generation. No additional commentary.
"""



PRODUCTS_PROMPT = """
You must generate two CSV files containing products that can be directly imported into Odoo's product.template model.

15 Customer Products with the following fields:
['name', 'default_code', 'list_price', 'type', 'description_sale', 'standard_price']

15 Supplier Products with the same fields:
['name', 'default_code', 'list_price', 'type', 'description_sale', 'standard_price']

Additional Instructions:
- No Empty Fields: No field should be left blank. 
- 'list_price' should only contain numeric values (e.g., 99.99) and not any symbols.
- Direct Import: The data must be importable directly into Odoo's product.template model.
- 'description_sale' should be in double quotes("TEXT").
- CSV Format: Both CSV files must start with ```csv and end with ```.
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
Company Description & Instructions:
__global_prompt__

Product Context:
Customer Products (what the company sells to customers):
```csv
__customer_products__
```

Supplier Products (what the company buys from suppliers):
```csv
__supplier_products__
```


You must generate two CSV files containing customers and suppliers that can be directly imported into Odoo's res.partner model.

Requirements:
15 Customers with the following fields:
['name', 'email', 'phone', 'street', 'street2', 'state', 'city', 'zip', 'country', 'website']

15 Suppliers with the following fields:
['name', 'email', 'phone', 'street', 'street2', 'state', 'city', 'zip', 'country', 'website']


You also need to generate 50 contact log chatter that can be attached to the generated customers and suppliers, with the following fields for the model mail.message:
['set', 'subject', 'body', 'sequence', 'from']
- 'set' should be from 1 to 5 indicating the set of communication, messages with the same 'set' number belong to the same communication thread.
- 'subject' should be a brief summary of the interaction, for example: "Follow-up call regarding recent order" or "Email exchange about product inquiry".
- 'body' should contain a realistic log of a contact with the customer or supplier. Be written in first-person ("we", "I") or second-person ("you")
- 'sequence' should be a number that indicates the order of the messages in the chatter.
- 'from' should be from ['customer', 'company'] indicating who initiated the contact. Don't add a 3rd value to from field, only these two values should be used.
- The messages must be divided equally between the customer and our company.
- The messages should represent realistic two-way business interactions.
- Each set should contain 10 messages that belong to the same communication thread, for example a follow-up call and the subsequent email exchange regarding the same topic.
- Dont add words like 'your name' ,'supplier name', 'date' or any particular customer or suppliers name to messages as that will make the message fixed to a particular customer or supplier.

Additional Instructions:
- No Empty Fields: No field should be left blank.
- zip should be unique for each partner to ensure realistic addresses.(Never use the same zip for two different partners)
- Direct Import: The data must be importable directly into Odoo's res.partner model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- A customer/supplier should not be used again as supplier/customer.
Business Realism:
- If the company is international, include customers and suppliers from various countries.
- If the company is local/regional, focus on nearby cities and regions.
- The total amount of customer invoices must be greater than the total amount of supplier invoices (the company is profitable).
- Customers and suppliers must be realistically aligned with the products they would buy/sell, based on the following product lists.

"""

CHILD_CONTACTS_PROMPT = """
Company Description & Instructions:
__global_prompt__

Customers and Suppliers Context:
Customers:
```csv
__customers__
```
Suppliers:
```csv
__suppliers__
```

You must generate a CSV file containing child contacts that can be directly imported into Odoo's res.partner model.


50 child contacts with the following fields:
['name', 'phone']
- 'name' must be a realistic and proper name for each child contact (should be a real persons name which aligns with the country of the company).
- 'phone' must be a unique phone number for each child contact.

Additional Instructions:
- No Empty Fields: No field should be left blank.
- Direct Import: The data must be importable directly into Odoo's res.partner model.
- CSV Format: CSV file must start with ```csv and end with ```.

"""

ASSETS_PROMPT = """
You need to generate a CSV file containing Assets that can be directly imported into Odoo's account.asset model.

Generate 10 assets for the account.asset model with the following fields:
['name', 'method', 'original_value', 'acquisition_date', 'salvage_value', 'account_depreciation_id', 'account_depreciation_expense_id', 'method_number']

- method must be selected from: ['linear', 'degressive', 'degressive_then_linear'] which corresponds to 'linear' being 'Straight Line', 'degressive' being 'Declining' and 'degressive_then_linear' being 'Declining then Straight Line'.
- account_depreciation_id is a account of fixed asset type.
- account_depreciation_expense_id is the expense type account.
- method_number(duration of the asset) is the number of periods (months or years) over which the asset will be depreciated.
- acquisition_date should be between today and 5 years ago.

Additional Instructions:
- Try to diversify the types of assets, for example include computers, furniture, vehicles, machinery, etc.
- No Empty Fields: No field should be left blank.
- Direct Importability: The data must be importable directly into Odoo’s account.asset model.
- CSV Format: Both CSV files must start with ```csv and end with ```.
- Business Relevance: Leads must align with the company’s industry as described in the global prompt.
- Accounts: The account_depreciation_id and account_depreciation_expense_id must match the existing accounts accordingly.
- Date: prorata_date must be after today (format: yyyy-mm-dd).
- The expense account should be relevant to the nature of the asset like a furniture should be linked to an office expense account.
- The expense account should not be the same for all assets, try to diversify.
- Also keep in mind that the depreciation time and value should be realistic, a computer will not be depreciated over 20 years for example.
- Dates should be realistic and spread across from the acquisition date.

Company Description & Instructions:
__global_prompt__

Here is list of existing accounts in the chart of accounts for this company, use the relevant accounts for fields account_depreciation_id and account_depreciation_expense_id and set the id of the account as output in the: CSV:

"""

ANALYTIC_ACCOUNTS_PROMPT = """
Company Description & Instructions:
__global_prompt__

You need to generate a CSV file containing Analytic Accounts that can be directly imported into Odoo's account.analytic.account model.

Generate 10 analytic accounts for the account.analytic.account model with the following fields:
['name', 'code']
- 5 accounts must be for purchase bills and 5 accounts must be for sales invoices.
- 'name' must be a realistic and proper name for each analytic account and should only contain text relevant to account 
and not sales or purchase and alos should not be related to product names.
- 'code' must be unique for each analytic account.
- Create 5 analytic accounts for purchase bills and 5 analytic accounts for sales invoices.
- Each analytic account must have A realistic and business-appropriate name.
A unique code following the format:
PREFIX-TYPE-XXX
where:
TYPE = PO for purchase-related accounts
TYPE = SO for sales-related accounts
XXX = a three-digit running number (e.g., 001, 002, etc.)
PREFIX can be any three-letter combination relevant to the account name/type respectively to each account.

Additional Instructions:
- No Empty Fields: No field should be left blank.
- All fields should be in double quotes to handle commas within the text("TEXT").
- Direct Importability: The data must be importable directly into Odoo’s account.analytic.account model.
- CSV Format: The CSV file must start with ```csv and end with ```.
- Business Relevance: Analytic accounts must align with the company’s industry as described in the global prompt.
- Code must be unique for each analytic account.

"""

BILLS_PROMPT = """
You must generate two CSV files containing customer and vendor invoices that can be directly imported into Odoo’s account.move model.

Requirements:
20 Customer Invoices with the following fields:
['partner_name', 'date', 'invoice_date_due', 'amount_untaxed', 'move_type', 'invoice_line_ids/quantity', 'invoice_line_ids/price_unit', 'invoice_line_ids/product_name']

10 Vendor Invoices with the same fields as above.

move_type must be 'in_invoice' for Vendor Invoices and 'out_invoices' for Customer Invoices


You also need to generate 5 Follow-up levels for the generated invoices, that can be directly imported into Odoo’s account_followup.followup.line model, with the following fields:
['name', 'mail_template_id', 'delay', 'create_activity', 'activity_type_id', 'activity_summary', 'activity_note']
- 'mail_template_id' should be one of the following: ['Customer Statement', 'Document: Document Share', 'Follow Up Report','Payment Reminder', 'Second reminder followup']
- 'delay' should be a realistic number of days after the due date of the invoice.
- 'schedule_activity' should be a boolean field with either 'True' or 'False' as value, and it should be set to 'True' for at least the last 2 follow-up levels.
- 'activity_type_id' should be one of the following: ['email', 'call', 'meeting']
- 'activity_summary' should be a brief summary of the activity that will be scheduled for the follow-up, for example: "Send payment reminder email to customer" or "Call customer to follow up on overdue invoice".
- 'activity_note' should provide additional details about the follow-up activity, for example: "Include invoice details and payment instructions in the email" or "Prepare a list of overdue invoices to discuss during the call".


Additional Instructions:
- No Empty Fields: No field should be left blank.
- Direct Import: The data must be importable directly into Odoo’s account.move model.
- CSV Format: CSV files must start with ```csv and end with ```.
- Use Existing Data: Use the products and partners defined in the previously generated CSV files.
- Invoiced products must match those listed in the product files.
- Partners must match those listed in the customer and supplier files.
- Transaction Consistency: Ensure invoiced products logically align with the partners. For example, a paper company should not invoice potatoes.
- Realistic Amounts: Invoice amounts should be realistic and consistent with product prices and invoiced quantities.


 The total of the customer invoices MUST BE SUPERIOR to the total of the vendor invoices


Invoice Dates:
- Dates should be realistic and spread across 3 months in future and 3 months in past today's date __today__.
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
