import os
import io
import json
import math
import logging
import boto3
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # Potential caution in production

s3_client = boto3.client('s3')

def optimize_image_size(img, target_size_mb, initial_scale=1.0):
    """
    Optimize the image size by binary searching for the optimal scaling factor
    so that the saved PNG meets the target size in megabytes.
    """
    target_bytes = target_size_mb * 1024 * 1024
    buffer = io.BytesIO()
    low, high = 0.1, 1.0
    best_img = None

    for _ in range(8):
        mid = (low + high) / 2
        new_width = int(img.width * mid)
        new_height = int(img.height * mid)
        resized = img.resize((new_width, new_height), Image.LANCZOS)

        buffer.seek(0)
        buffer.truncate()
        resized.save(buffer, format='PNG', optimize=True, compress_level=9)
        current_size = buffer.tell()

        if current_size <= target_bytes:
            best_img = resized
            low = mid
        else:
            high = mid

    return best_img or img

def convert_tiff_to_png_stream(input_stream, target_size_mb=3):
    """
    Converts a TIFF image (provided as an in-memory stream) to a PNG,
    compressing/resizing it so that the final file is at or below the target size.
    """
    try:
        input_stream.seek(0)
        with Image.open(input_stream) as img:
            img = img.convert('RGB')
            
            buffer = io.BytesIO()
            img.save(buffer, format='PNG', optimize=True, compress_level=9)
            initial_size_mb = buffer.tell() / (1024 * 1024)

            if initial_size_mb <= target_size_mb:
                buffer.seek(0)
                return buffer, initial_size_mb, (img.width, img.height)

            scale_estimate = math.sqrt(target_size_mb / initial_size_mb)
            optimized_img = optimize_image_size(img, target_size_mb, scale_estimate)

            final_buffer = io.BytesIO()
            optimized_img.save(final_buffer, format='PNG', optimize=True, compress_level=9)
            final_size_mb = final_buffer.tell() / (1024 * 1024)

            return final_buffer, final_size_mb, (optimized_img.width, optimized_img.height)
    except Exception as e:
        logging.error(f"Error in convert_tiff_to_png_stream: {e}")
        raise

def lambda_handler(event, context):
    """
    Triggered by S3 events on the 'raw/' folder.
    Downloads the file, converts to PNG under the 'compressed/' folder,
    writes errors to 'error/' folder if any exceptions occur.
    """
    bucket_name = os.environ.get("BUCKET_NAME")
    compressed_folder = os.environ.get("COMPRESSED_FOLDER", "compressed")
    error_folder = os.environ.get("ERROR_FOLDER", "error")
    target_size_mb = float(os.environ.get("COMPRESSION_TARGET_MB", "3"))

    logging.info("Event: %s", json.dumps(event))

    for record in event.get('Records', []):
        obj = record['s3']
        source_bucket = obj['bucket']['name']
        source_key = obj['object']['key']

        # Only process .tif or .tiff
        if not source_key.lower().endswith(('.tif', '.tiff')):
            logging.info(f"Skipping non-TIFF file: {source_key}")
            continue

        file_basename = os.path.splitext(os.path.basename(source_key))[0]
        new_object_key = f"{compressed_folder}/{file_basename}.png"

        try:
            # Download the original TIFF
            original_stream = io.BytesIO()
            s3_client.download_fileobj(source_bucket, source_key, original_stream)

            # Convert to compressed PNG
            converted_stream, image_size_mb, dimensions = convert_tiff_to_png_stream(
                original_stream,
                target_size_mb=target_size_mb
            )

            # Upload the converted file
            converted_stream.seek(0)
            s3_client.upload_fileobj(converted_stream, bucket_name, new_object_key)
            logging.info(
                f"Uploaded compressed file to s3://{bucket_name}/{new_object_key} | "
                f"Size: {image_size_mb:.2f} MB | Dimensions: {dimensions[0]}x{dimensions[1]}"
            )

        except Exception as e:
            error_message = f"Error processing file {source_key}: {e}"
            logging.error(error_message)
            error_file_name = f"{file_basename}.txt"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=f"{error_folder}/{error_file_name}",
                Body=error_message
            )
