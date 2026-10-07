
BACKGROUND_PROMPT = """
__global_prompt__


Based on the information you have of the company described previously, your goal is to generate an image to use as the background of the Odoo of the aforementioned company.
You must generate a prompt that will be sent to another mistral model to generate the picture. The model you will send your prompt to must make a picture that is related to the field of the company, has low luminosity, and is quite simple in design
Do not invent information that you do not have. If you lack information, make the image generic in those aspects
Make sure the model does NOT generate any text

Generate ONLY the prompt and NOTHING ELSE
"""

HR_PICTURES_PROMPT = """
Generate a profile picture of an Employee on Odoo. The employee's name is __employee__.
This employee occupies the post __job__ in the Department __department__ of the company __name__.
Invent the profile picture of this employee and make him wear the company's shirt, with the company's name \"__name__\" written on it.
The profile picture must be relatively professional and taken from the front.
"""

PRODUCT_PICTURES_PROMPT = """
Generate a product image to use as the main image of a product in Odoo. The product's name is __product__.
This product is sold by the company __name__.
Invent the image of this product and make sure it is related to the field of the company.
The image must be simple and clean, with a white background.
The images shouldn't be one that is already created for another product.
"""

PDF_PROMPT = """
Generate a supplier invoice for demonstration purposes.  
Supplier: name: __supplier_name__, city: __supplier_city__ , country: __supplier_country__
Client: name: __name__, city: __city__ , country: __country__

The sold product is a __product_name__ in __product_qty__ units at the price of __product_price__ per unit.
The total (tax included) of the invoice is __tax_total__. Do NOT specify the percentage of the VAT.
The invoice date is __bill_date__ and the invoice is due for __due_date__.
The invoice must be generated in HTML format and properly formatted to be subsequently converted to PDF using the pdfkit library.  
Do NOT generate images.
"""

ARTICLE_IMAGES_PROMPT = """
Generate an image to be used as the main image of a article in Odoo. The article's title is __article_title__.
This article is published on the blog of the company __name__.
Invent the image of this article and make sure it is related to the field of the company and the title of the article.
The images shouldn't be one that is already created for another article.
"""
