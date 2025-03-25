import runpod
from runpod.serverless.utils import rp_upload
import json
import urllib.request
import urllib.parse
import time
import os
import requests
import base64
from io import BytesIO
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Time to wait between API check attempts in milliseconds
COMFY_API_AVAILABLE_INTERVAL_MS = 50
# Maximum number of API check attempts
COMFY_API_AVAILABLE_MAX_RETRIES = 500
# Time to wait between poll attempts in milliseconds
COMFY_POLLING_INTERVAL_MS = int(os.environ.get("COMFY_POLLING_INTERVAL_MS", 250))
# Maximum number of poll attempts
COMFY_POLLING_MAX_RETRIES = int(os.environ.get("COMFY_POLLING_MAX_RETRIES", 500))
# Host where ComfyUI is running
COMFY_HOST = "127.0.0.1:8188"
# Enforce a clean state after each job is done
# see https://docs.runpod.io/docs/handler-additional-controls#refresh-worker
REFRESH_WORKER = os.environ.get("REFRESH_WORKER", "false").lower() == "true"
# Directory containing workflow JSON files
WORKFLOW_DIR = "/runpod-volume/workflows"

def load_workflow_by_name(workflow_name):
    """
    Load a workflow from the predefined workflows directory.
    
    Args:
        workflow_name (str): The name of the workflow file
        
    Returns:
        dict: The loaded workflow as a dictionary, or None if not found
    """
    # Ensure filename has .json extension
    if not workflow_name.endswith('.json'):
        workflow_name = f"{workflow_name}.json"
        
    workflow_path = os.path.join(WORKFLOW_DIR, workflow_name)
    
    if not os.path.exists(workflow_path):
        logging.error(f"Workflow not found: {workflow_path}")
        return None
        
    try:
        with open(workflow_path, 'r') as f:
            workflow_data = json.load(f)
            logging.info(f"Successfully loaded workflow: {workflow_name}")
            
            # Check if the workflow is already in the expected format
            if isinstance(workflow_data, dict) and not "prompt" in workflow_data:
                return workflow_data
            # If it's in ComfyUI API format with 'prompt'
            elif isinstance(workflow_data, dict) and "prompt" in workflow_data:
                return workflow_data["prompt"] 
            # If it's wrapped in an input structure
            elif "input" in workflow_data and "workflow" in workflow_data["input"]:
                return workflow_data["input"]["workflow"]
            else:
                logging.warning(f"Workflow format for {workflow_name} is unknown, returning as-is")
                return workflow_data
    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON in workflow file {workflow_name}: {str(e)}")
        return None
    except Exception as e:
        logging.error(f"Error loading workflow {workflow_name}: {str(e)}")
        return None

def validate_input(job_input):
    """
    Validates the input for the handler function.

    Args:
        job_input (dict): The input data to validate.

    Returns:
        tuple: A tuple containing the validated data and an error message, if any.
               The structure is (validated_data, error_message).
    """
    # Validate if job_input is provided
    if job_input is None:
        return None, "Please provide input"

    # Check if input is a string and try to parse it as JSON
    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError:
            return None, "Invalid JSON format in input"

    # Validate that either 'workflow' or 'workflow_name' is provided
    workflow = job_input.get("workflow")
    workflow_name = job_input.get("workflow_name")
    
    if not workflow and not workflow_name:
        return None, "Missing 'workflow' or 'workflow_name' parameter"
    
    # If workflow_name is provided, load the workflow
    if workflow_name:
        workflow = load_workflow_by_name(workflow_name)
        if not workflow:
            return None, f"Could not load workflow: {workflow_name}"

    # Validate 'images' in input, if provided
    images = job_input.get("images")
    if images is not None:
        if not isinstance(images, list) or not all(
            "name" in image and "image" in image for image in images
        ):
            return (
                None,
                "'images' must be a list of objects with 'name' and 'image' keys",
            )

    # Return validated data and no error
    return {"workflow": workflow, "images": images}, None

def check_server(url, retries=500, delay=50):
    """
    Check if a server is reachable via HTTP GET request

    Args:
    - url (str): The URL to check
    - retries (int, optional): The number of times to attempt connecting to the server. Default is 50
    - delay (int, optional): The time in milliseconds to wait between retries. Default is 500

    Returns:
    bool: True if the server is reachable within the given number of retries, otherwise False
    """

    for i in range(retries):
        try:
            response = requests.get(url)

            # If the response status code is 200, the server is up and running
            if response.status_code == 200:
                print(f"runpod-worker-comfy - API is reachable")
                return True
        except requests.RequestException as e:
            # If an exception occurs, the server may not be ready
            pass

        # Wait for the specified delay before retrying
        time.sleep(delay / 1000)

    print(
        f"runpod-worker-comfy - Failed to connect to server at {url} after {retries} attempts."
    )
    return False


def upload_images(images):
    """
    Upload a list of base64 encoded images to the ComfyUI server using the /upload/image endpoint.
    """
    if not images:
        return {"status": "success", "message": "No images to upload", "details": []}

    responses = []
    upload_errors = []

    print(f"runpod-worker-comfy - image(s) upload")

    for image in images:
        name = image["name"]
        image_data = image["image"]
        
        # Handle data URL format
        if image_data.startswith("data:"):
            base64_prefix_end = image_data.find(",")
            if base64_prefix_end != -1:
                image_data = image_data[base64_prefix_end + 1:]
                logging.info(f"Extracted base64 data from data URL for {name}")
        
        try:
            blob = base64.b64decode(image_data)
            
            # Prepare the form data
            files = {
                "image": (name, BytesIO(blob), "image/png"),
                "overwrite": (None, "true"),
            }

            # POST request to upload the image
            response = requests.post(f"http://{COMFY_HOST}/upload/image", files=files)
            if response.status_code != 200:
                upload_errors.append(f"Error uploading {name}: {response.text}")
            else:
                responses.append(f"Successfully uploaded {name}")
        except Exception as e:
            logging.error(f"Failed to decode base64 for {name}: {str(e)}")
            upload_errors.append(f"Error decoding {name}: {str(e)}")

    if upload_errors:
        print(f"runpod-worker-comfy - image(s) upload with errors")
        return {
            "status": "error",
            "message": "Some images failed to upload",
            "details": upload_errors,
        }

    print(f"runpod-worker-comfy - image(s) upload complete")
    return {
        "status": "success",
        "message": "All images uploaded successfully",
        "details": responses,
    }


def queue_workflow(workflow):
    """
    Queue a workflow to be processed by ComfyUI

    Args:
        workflow (dict): A dictionary containing the workflow to be processed

    Returns:
        dict: The JSON response from ComfyUI after processing the workflow
    """

    # The top level element "prompt" is required by ComfyUI
    data = json.dumps({"prompt": workflow}).encode("utf-8")

    req = urllib.request.Request(f"http://{COMFY_HOST}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_history(prompt_id):
    """
    Retrieve the history of a given prompt using its ID

    Args:
        prompt_id (str): The ID of the prompt whose history is to be retrieved

    Returns:
        dict: The history of the prompt, containing all the processing steps and results
    """
    with urllib.request.urlopen(f"http://{COMFY_HOST}/history/{prompt_id}") as response:
        return json.loads(response.read())


def base64_encode(img_path):
    """
    Returns base64 encoded image.

    Args:
        img_path (str): The path to the image

    Returns:
        str: The base64 encoded image
    """
    with open(img_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return f"{encoded_string}"


def process_output_images(outputs, job_id):
    """Process output images from ComfyUI workflow execution"""
    # The path where ComfyUI stores the generated images
    COMFY_OUTPUT_PATH = os.environ.get("COMFY_OUTPUT_PATH", "/comfyui/output")
    
    # Collect all generated image paths
    image_paths = []
    
    # Method 1: Parse from outputs
    for node_id, node_output in outputs.items():
        if "images" in node_output:
            for image in node_output["images"]:
                image_path = os.path.join(COMFY_OUTPUT_PATH, image.get("subfolder", ""), image["filename"])
                if os.path.exists(image_path):
                    image_paths.append(image_path)
                    logging.info(f"Found image in outputs: {image_path}")
    
    # Method 2: If no images found, try scanning the output directory for recent files
    if not image_paths:
        logging.info("No images found in outputs, scanning output directory for recent files")
        current_time = time.time()
        # Look for files created in the last 30 seconds
        for root, dirs, files in os.walk(COMFY_OUTPUT_PATH):
            for file in files:
                if file.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    file_path = os.path.join(root, file)
                    file_time = os.path.getmtime(file_path)
                    # If file was created in the last 30 seconds
                    if current_time - file_time < 30:
                        image_paths.append(file_path)
                        logging.info(f"Found recent image in output directory: {file_path}")
    
    # Process the first image found
    if image_paths:
        local_image_path = image_paths[0]  # Process the first image for now
        
        logging.info(f"Processing output image: {local_image_path}")
        
        if os.environ.get("BUCKET_ENDPOINT_URL", False):
            # URL to image in AWS S3
            image = rp_upload.upload_image(job_id, local_image_path)
            logging.info("Image was generated and uploaded to AWS S3")
        else:
            # base64 image
            image = base64_encode(local_image_path)
            logging.info("Image was generated and converted to base64")
            
        return {
            "status": "success",
            "message": image,
            "path": local_image_path,
            "all_images": image_paths  # Return all found images for reference
        }
    else:
        logging.error("No output images found")
        return {
            "status": "error",
            "message": "No output images found in the output directory"
        }


def handler(job):
    """The main function that handles a job of generating an image."""
    job_input = job["input"]
    
    logging.info(f"Processing job: {job['id']}")

    # Make sure that the input is valid
    validated_data, error_message = validate_input(job_input)
    if error_message:
        logging.error(f"Input validation error: {error_message}")
        return {"error": error_message}

    # Extract validated data
    workflow = validated_data["workflow"]
    images = validated_data.get("images")

    # Make sure that the ComfyUI API is available
    check_server(
        f"http://{COMFY_HOST}",
        COMFY_API_AVAILABLE_MAX_RETRIES,
        COMFY_API_AVAILABLE_INTERVAL_MS,
    )

    # Find and inject images directly into LoadImageFromBase64 nodes
    if images:
        base64_nodes_found = False
        for node_id, node_data in workflow.items():
            if node_data.get("class_type") in ["LoadImageFromBase64", "Base64ToImage"]:
                # We found a node that can take base64 image data directly
                for image in images:
                    # Get image data, handle data URL format
                    image_data = image["image"]
                    if image_data.startswith("data:"):
                        base64_prefix_end = image_data.find(",")
                        if base64_prefix_end != -1:
                            image_data = image_data[base64_prefix_end + 1:]
                            logging.info(f"Extracted base64 data from data URL for injecting into node")
                    
                    # Inject the image data into the workflow
                    workflow[node_id]["inputs"]["data"] = image_data
                    logging.info(f"Injected base64 image data into node {node_id} of type {node_data.get('class_type')}")
                    base64_nodes_found = True
                    break  # Only inject into the first matching node for now
                
                if base64_nodes_found:
                    break  # Stop searching for nodes after first injection
        
        # If we didn't find any Base64 nodes, upload images normally
        if not base64_nodes_found:
            upload_result = upload_images(images)
            if upload_result["status"] == "error":
                logging.error(f"Image upload error: {upload_result['message']}")
                return upload_result

    # Queue the workflow
    try:
        queued_workflow = queue_workflow(workflow)
        prompt_id = queued_workflow["prompt_id"]
        logging.info(f"Queued workflow with ID {prompt_id}")
    except Exception as e:
        logging.error(f"Error queuing workflow: {str(e)}")
        return {"error": f"Error queuing workflow: {str(e)}"}

    # Rest of the function remains the same...

    # Poll for completion
    logging.info(f"Waiting until image generation is complete")
    retries = 0
    try:
        while retries < COMFY_POLLING_MAX_RETRIES:
            history = get_history(prompt_id)

            # Exit the loop if we have found the history
            if prompt_id in history and history[prompt_id].get("outputs"):
                break
            else:
                # Wait before trying again
                time.sleep(COMFY_POLLING_INTERVAL_MS / 1000)
                retries += 1
        else:
            logging.error("Max retries reached waiting for image generation")
            return {"error": "Max retries reached while waiting for image generation"}
    except Exception as e:
        logging.error(f"Error waiting for image generation: {str(e)}")
        return {"error": f"Error waiting for image generation: {str(e)}"}

    # Get the generated image and return it as URL in an AWS bucket or as base64
    images_result = process_output_images(history[prompt_id].get("outputs"), job["id"])
    
    logging.info(f"Image processing complete with status: {images_result['status']}")
    result = {**images_result, "refresh_worker": REFRESH_WORKER}

    return result

# Start the handler only if this script is run directly
if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})