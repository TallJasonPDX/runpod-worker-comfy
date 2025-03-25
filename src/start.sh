#!/usr/bin/env bash

# Use libtcmalloc for better memory management
TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1)"
export LD_PRELOAD="${TCMALLOC}"

# Ensure ComfyUI input directory exists
mkdir -p /comfyui/input

# Function to copy custom files and log details
copy_custom_files() {
    echo "======= CUSTOM FILES COPYING PROCESS START ======="
    
    # Show network volume contents for debugging
    echo "Contents of network volume custom_nodes directory:"
    if [ -d "/runpod-volume/custom_nodes" ]; then
        ls -la /runpod-volume/custom_nodes/
    else
        echo "Directory /runpod-volume/custom_nodes/ does not exist."
        mkdir -p /runpod-volume/custom_nodes
    fi
    
    # Copy custom nodes from network volume to ComfyUI
    echo "Copying custom nodes from network volume to ComfyUI:"
    if [ -d "/runpod-volume/custom_nodes" ]; then
        for file in /runpod-volume/custom_nodes/*; do
            if [ -f "$file" ]; then
                filename=$(basename "$file")
                echo "  - Copying $filename to /comfyui/custom_nodes/"
                cp "$file" /comfyui/custom_nodes/
                chmod 755 /comfyui/custom_nodes/"$filename"
                echo "  - File permissions after copy:"
                ls -la /comfyui/custom_nodes/"$filename"
            fi
        done
    fi
    
    # Show ComfyUI custom_nodes directory after copying
    echo "Contents of ComfyUI custom_nodes directory after copying:"
    ls -la /comfyui/custom_nodes/
    
    # Copy input files from network volume
    echo "Copying input files from network volume to ComfyUI:"
    if [ -d "/runpod-volume/input" ]; then
        for file in /runpod-volume/input/*; do
            if [ -f "$file" ]; then
                filename=$(basename "$file")
                echo "  - Copying $filename to /comfyui/input/"
                cp "$file" /comfyui/input/
            elif [ -d "$file" ]; then
                dirname=$(basename "$file")
                echo "  - Copying directory $dirname and its contents"
                mkdir -p "/comfyui/input/$dirname"
                cp -r "$file"/* "/comfyui/input/$dirname/"
            fi
        done
    else
        echo "Directory /runpod-volume/input/ does not exist."
    fi
    
    # Show ComfyUI input directory after copying
    echo "Contents of ComfyUI input directory after copying:"
    ls -la /comfyui/input/
    
    echo "======= CUSTOM FILES COPYING PROCESS COMPLETE ======="
}

# Run the copy function
copy_custom_files

# Copy overlay images from network volumeto ComfyUI input folder
if [ -d "/runpod-volume/input" ]; then
  echo "runpod-worker-comfy: Copying input files from network volume"
  cp -r /runpod-volume/input/* /comfyui/input/
  echo "runpod-worker-comfy: Input files copied successfully"
fi

# Serve the API and don't shutdown the container
if [ "$SERVE_API_LOCALLY" == "true" ]; then
    echo "runpod-worker-comfy: Starting ComfyUI"
    python3 /comfyui/main.py --disable-auto-launch --disable-metadata --listen &

    echo "runpod-worker-comfy: Starting RunPod Handler"
    python3 -u /rp_handler.py --rp_serve_api --rp_api_host=0.0.0.0
else
    echo "runpod-worker-comfy: Starting ComfyUI"
    python3 /comfyui/main.py --disable-auto-launch --disable-metadata &

    echo "runpod-worker-comfy: Starting RunPod Handler"
    python3 -u /rp_handler.py
fi