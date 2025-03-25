#!/bin/bash

# Copy all custom nodes from network volume if directory exists
if [ -d "/runpod-volume/custom_nodes" ]; then
  echo "Copying custom nodes from network volume to ComfyUI:"
  for file in /runpod-volume/custom_nodes/*; do
    if [ -f "$file" ]; then
      filename=$(basename "$file")
      echo "  - Copying $filename"
      cp "$file" /comfyui/custom_nodes/
    fi
  done
fi

# Copy all input files from network volume if directory exists
if [ -d "/runpod-volume/input" ]; then
  echo "Copying input files from network volume to ComfyUI:"
  for file in /runpod-volume/input/*; do
    if [ -f "$file" ]; then
      filename=$(basename "$file")
      echo "  - Copying $filename"
      cp "$file" /comfyui/input/
    elif [ -d "$file" ]; then
      dirname=$(basename "$file")
      echo "  - Copying directory $dirname and its contents"
      mkdir -p "/comfyui/input/$dirname"
      cp -r "$file"/* "/comfyui/input/$dirname/"
    fi
  done
fi

echo "Network volume copy operations completed"