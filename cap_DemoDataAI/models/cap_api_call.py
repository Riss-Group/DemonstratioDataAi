from mistralai import Mistral

def mistral_api_call(mistral_model: str, message: str, api_key: str, language: str):
    client = Mistral(api_key=api_key)

    chat_response = client.chat.complete(
        model=mistral_model,
        response_format={
            "type": "text",
        },
        max_tokens=15000,
        temperature=0.1,
        messages=[
            {
                "role": "user",
                "content": message
            },
            {"role": "system",
             "content": "Generate this message in " + language}
        ]
    )
    print(chat_response.usage)
    return chat_response
