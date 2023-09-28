import logging
import requests
import traceback

from flask import Flask, request
from langchain.chains.openai_functions import create_structured_output_chain
from langchain.chat_models import ChatOpenAI
from langchain.prompts import ChatPromptTemplate

from langchain.utilities.openapi import OpenAPISpec
from utils.base import try_to_match_and_call_api_endpoint
from models.models import AiResponseFormat
from routes.workflow.workflow_controller import workflow
import json
from typing import Any, Tuple
from prompts.base import api_base_prompt, non_api_base_prompt
from routes.workflow.workflow_service import run_workflow
from routes.workflow.typings.run_workflow_input import WorkflowData
from utils.detect_multiple_intents import hasSingleIntent, hasMultipleIntents
import os
from dotenv import load_dotenv


load_dotenv()
shared_folder = os.getenv("SHARED_FOLDER", "/app/shared_data/")
logging.basicConfig(level=logging.DEBUG)


app = Flask(__name__)

app.register_blueprint(workflow, url_prefix="/workflow")


## TODO: Implement caching for the swagger file content (no need to load it everytime)
@app.route("/handle", methods=["POST", "OPTIONS"])
def handle():
    data = request.get_json()
    text = data.get("text")
    swagger_url = data.get("swagger_url")
    base_prompt = data.get("base_prompt")
    headers = data.get("headers", {})
    server_base_url = data.get("server_base_url")

    if not base_prompt:
        return json.dumps({"error": "base_prompt is required"}), 400

    if not text:
        return json.dumps({"error": "text is required"}), 400

    if not swagger_url:
        return json.dumps({"error": "swagger_url is required"}), 400

    if swagger_url.startswith("https://"):
        pass
    else:
        swagger_url = shared_folder + swagger_url

    print(f"swagger_url::{swagger_url}")
    try:
        if hasMultipleIntents(text):
            result = run_workflow(
                WorkflowData(text, swagger_url, headers, server_base_url)
            )

            return result
    except Exception as e:
        raise e

    if swagger_url.startswith("https://"):
        response = requests.get(swagger_url)
        if response.status_code == 200:
            swagger_text = response.text
        else:
            return json.dumps({"error": "Failed to fetch Swagger content"}), 500
    else:
        try:
            with open(swagger_url, "r") as file:
                swagger_text = file.read()
        except FileNotFoundError:
            return json.dumps({"error": "File not found"}), 404

    swagger_spec = OpenAPISpec.from_text(swagger_text)

    try:
        json_output = try_to_match_and_call_api_endpoint(swagger_spec, text, headers)
    except Exception as e:
        logging.error(f"Failed to call or map API endpoint: {str(e)}")
        logging.error("Exception traceback:\n" + traceback.format_exc())
        json_output = None

    llm = ChatOpenAI(model="gpt-3.5-turbo-0613", temperature=0)

    if json_output is None:
        prompt_msgs = non_api_base_prompt(base_prompt, text)

    else:
        prompt_msgs = api_base_prompt(base_prompt, text, json_output)

    prompt = ChatPromptTemplate(messages=prompt_msgs)
    chain = create_structured_output_chain(AiResponseFormat, llm, prompt, verbose=False)
    chain_output = chain.run(question=text)

    return json.loads(json.dumps(chain_output.dict())), 200


@app.errorhandler(500)
def internal_server_error(error: Any) -> Tuple[str, int]:
    # Log the error to the console
    print(error)
    return "Internal Server Error", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, debug=True)
