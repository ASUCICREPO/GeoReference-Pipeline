import os
import io
import json
import base64
import boto3
import requests
import pandas as pd
import geojson
from geopy.geocoders import Nominatim
from github import Github
from botocore.exceptions import ClientError
from xml.etree import ElementTree

# Global clients
s3_client = boto3.client("s3")

# If you need region-specific clients for Bedrock:
#   bedrock_region = os.environ.get("BEDROCK_REGION", "us-west-2")
#   bedrock_client = boto3.client("bedrock-runtime", region_name=bedrock_region)
# For simplicity, let's create the client lazily after reading env variables in the handler.

geolocator = Nominatim(user_agent="myGeocoder",timeout=10)
# Colorado bounding coordinates (WGS84)
# colorado_bbox = [-109.060253, 36.992426, -102.041524, 41.003444]

# geolocator = Nominatim(
#     user_agent="colorado_water_maps",
#     timeout=10,
#     viewbox=colorado_bbox, 
#     bounded=True
# )


def invoke_bedrock_model_claude_multimodal(bedrock_client, content_image_b64, text_prompt, model_id, max_length=4096):
    """
    Sends an image (base64-encoded) plus a text prompt to Claude via Bedrock.
    Returns the text response.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": content_image_b64
                    }
                },
                {
                    "type": "text",
                    "text": text_prompt
                }
            ]
        }
    ]

    request_body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_length,
        "temperature": 0.5,
        "messages": messages
    })

    try:
        response = bedrock_client.invoke_model(
            modelId=model_id,
            body=request_body
        )
        model_response = json.loads(response["body"].read())
        response_text = model_response["content"][0]["text"]
        return response_text.strip()
    except (ClientError, Exception) as e:
        print(f"ERROR: Could not invoke '{model_id}' via Bedrock. Reason: {e}")
        raise

def get_coordinates_from_township(township_str, country="United States of America", state="Colorado"):
    """
    Calls a SOAP endpoint to convert a township-range string (which must include a section) into lat/lon.
    """
    soap_request = f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" 
               xmlns:xsd="http://www.w3.org/2001/XMLSchema" 
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Georef2 xmlns="http://geo-locate.org/webservices/">
      <Country>{country}</Country>
      <State>{state}</State>
      <County></County>
      <LocalityString>{township_str}</LocalityString>
      <HwyX>true</HwyX>
      <FindWaterbody>true</FindWaterbody>
      <RestrictToLowestAdm>false</RestrictToLowestAdm>
      <doUncert>true</doUncert>
      <doPoly>true</doPoly>
      <displacePoly>false</displacePoly>
      <polyAsLinkID>false</polyAsLinkID>
      <LanguageKey>0</LanguageKey>
    </Georef2>
  </soap:Body>
</soap:Envelope>'''

    try:
        response = requests.post(
            url="http://www.geo-locate.org/webservices/geolocatesvcv2/geolocatesvc.asmx",
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "http://geo-locate.org/webservices/Georef2"
            },
            data=soap_request
        )

        if response.status_code == 200:
            root = ElementTree.fromstring(response.content)
            namespace = {'ns': 'http://geo-locate.org/webservices/'}
            result_set = root.find('.//ns:ResultSet', namespace)
            if result_set is not None:
                coord = result_set.find('ns:WGS84Coordinate', namespace)
                if coord is not None:
                    lat = float(coord.find('ns:Latitude', namespace).text)
                    lon = float(coord.find('ns:Longitude', namespace).text)
                    precision = result_set.find('ns:Precision', namespace).text
                    score = int(result_set.find('ns:Score', namespace).text)
                    uncertainty = result_set.find('ns:UncertaintyRadiusMeters', namespace).text
                    return {
                        'latitude': lat,
                        'longitude': lon,
                        'precision': precision,
                        'score': score,
                        'uncertainty_radius_m': uncertainty
                    }
        return None
    except Exception as e:
        print(f"ERROR calling SOAP API for {township_str}. Exception: {e}")
        return None

def upload_to_github(gh_client, repo_name, file_path, content, commit_message):
    """
    Upload (or update) a file in GitHub.
    """
    repo = gh_client.get_user().get_repo(repo_name)
    try:
        existing_contents = repo.get_contents(file_path)
        repo.update_file(file_path, commit_message, content, existing_contents.sha)
    except Exception:
        repo.create_file(file_path, commit_message, content)
    return f"https://github.com/{gh_client.get_user().login}/{repo_name}/blob/main/{file_path}"

def lambda_handler(event, context):
    bucket_name = os.environ.get("BUCKET_NAME")
    error_folder = os.environ.get("ERROR_FOLDER", "error")
    analysis_folder = os.environ.get("ANALYSIS_FOLDER", "analysis")

    github_token = os.environ.get("GITHUB_TOKEN")
    github_repo_name = os.environ.get("GITHUB_REPO_NAME", "water_resources_geojson")
    bedrock_model_id = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5")
    bedrock_region = os.environ.get("BEDROCK_REGION", "us-west-2")

    prompt_file_name = os.environ.get("PROMPT_FILE_NAME", "prompt.py")

    # Initialize bedrock client with region
    bedrock_client = boto3.client("bedrock-runtime", region_name=bedrock_region)

    from github import Github
    gh_client = Github(github_token)

    # Attempt to load the prompt from a local file in the Lambda package
    # (You might have a better approach, e.g., SSM Parameter, or stored in S3)
    # from .prompt import PROMPT
    prompt_text = """Analyze the uploaded map image thoroughly to identify all relevant details. Then, return a strictly formatted JSON object with the **exact** structure and keys below (and nothing else):

{
  "map_description": "string",
  "township_range": [
    // An array of valid township-range strings. A township-range string is considered valid only if it contains:
    //   1. A Township value in the format "T<number>N" or "T<number>S"
    //   2. A Range value in the format "R<number>E" or "R<number>W"
    //   3. Ideally, a Section value formatted as "Section <number>"
    // For example: "T1N R1E Section 1" or "T2S R3W Section 15".
    // If you cannot find any complete township-range information, return an empty array.
  ],
  "county": "string", 
    // If multiple counties apply, join them into one string separated by a semicolon and a space (e.g., "Teller County (Colo.); El Paso County (Colo.)").
  "water_resources": [
    // An array with as many water resources as you can identify from the map.
    // For each water resource, include the following keys:
    {
      "name": "string",
      "description": "string",
      "feature_type": "reservoir, dam, river, lake, creek, etc.",
      "township_range": "string" 
        // If you can identify a valid township-range (i.e. including both T and R values, and a Section number), put it here.
        // Otherwise, leave this field as an empty string "".
    },
    ...
  ]
}

Instructions and notes:
1. **map_description**: Provide a comprehensive description of the map, including its features, landmarks, and any notable context.
2. **township_range**: Only include strings that contain both a valid Township and Range value (e.g., "T8N R70W") along with a Section number (e.g., "Section 15"). Do not include partial entries like "T8N" alone.
3. **county**: Clearly specify the county (or counties) where the map is located. If more than one county is relevant, separate them using a semicolon and a space.
4. **water_resources**:
   - Identify every water resource visible on the map.
   - For each, include "name", "description", and "feature_type".
   - If the water resource has a visible township-range that is complete (including T, R), include it in the "township_range" field; if not, use an empty string.
5. Return **only** valid JSON without any extra commentary, explanations, or text outside of the JSON object.
"""
    # try:
    #     with open(os.path.join(os.path.dirname(__file__), prompt_file_name), "r") as f:
    #         prompt_text = f.read()
    # except Exception:
    #     # fallback to an inline prompt if needed
    #     prompt_text = """
    #     Analyze the uploaded map image thoroughly...
    #     ...
    #     (Your fallback prompt here)
    #     """

    try:
        record = event['Records'][0]
        s3_info = record['s3']
        source_bucket = s3_info['bucket']['name']
        object_key = s3_info['object']['key']
        image_name = os.path.basename(object_key)

        local_image_path = f"/tmp/{image_name}"
        s3_client.download_file(source_bucket, object_key, local_image_path)

        # Convert image to base64
        with open(local_image_path, "rb") as f:
            content_image_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Invoke the Bedrock model with the base64 image + prompt
        llm_response = invoke_bedrock_model_claude_multimodal(
            bedrock_client=bedrock_client,
            content_image_b64=content_image_b64,
            text_prompt=prompt_text,
            model_id=bedrock_model_id,
            max_length=2048
        )

        parsed_data = json.loads(llm_response)
        print(parsed_data)
        map_description = parsed_data.get("map_description", "")
        map_township_ranges = parsed_data.get("township_range", [])
        county_str = parsed_data.get("county", "")
        water_resources = parsed_data.get("water_resources", [])

        # Tweak T/R if needed
        updated_map_township = []
        for ts in map_township_ranges:
            if "Section" not in ts:
                ts = ts.strip() + " Section 15"
            updated_map_township.append(ts)

        # Convert T/R to coordinates
        township_coords = []
        for ts in updated_map_township:
            coord = get_coordinates_from_township(ts)
            if coord:
                coord["township_range_reference"] = ts
                township_coords.append(coord)

        # Water resources
        water_features = []
        water_feature_list = []
        water_resource_coords = []

        for resource in water_resources:
            name = resource.get("name", "").strip()
            feature_type = resource.get("feature_type", "").strip()
            ts = resource.get("township_range", "").strip()

            coord_source = ""
            coord = None

            if ts:
                if "Section" not in ts:
                    ts = ts + " Section 15"
                coord = get_coordinates_from_township(ts)
                coord_source = f"Township-Range: {ts}" if coord else ""

            if not coord:
                # Attempt geocoding
                try:
                    query = ts if ts else name +", Colorado, USA"
                    
                    if not query:
                        raise ValueError("No township or name to geocode.")
                    location = geolocator.geocode(query,country_codes="us")
                    if location:
                        coord = {
                            'latitude': location.latitude,
                            'longitude': location.longitude
                        }
                        coord_source = f"Geocoded from name: {name}"
                except Exception as geocode_e:
                    print(f"Warning: Could not geocode water resource '{name}': {geocode_e}")

            if coord:
                water_resource_coords.append(coord)
                point_feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [coord['longitude'], coord['latitude']]
                    },
                    "properties": {
                        "name": name,
                        "type": feature_type,
                        "coordinate_source": coord_source,
                        "township_range_used": ts if ts else ""
                    }
                }
                water_features.append(point_feature)
                water_feature_list.append(f"{name} ({feature_type})")
            else:
                water_feature_list.append(f"{name} ({feature_type})")

        # Compute bounding box
        all_coords = []
        if township_coords:
            all_coords = township_coords
            bounding_box_source = "Derived from Map-Level Township Ranges"
        elif water_resource_coords:
            all_coords = water_resource_coords
            bounding_box_source = "Fallback from Water Resource Coordinates"
        else:
            bounding_box_source = "No coordinates available"

        if all_coords:
            lats = [c['latitude'] for c in all_coords]
            lons = [c['longitude'] for c in all_coords]
            west, east = min(lons), max(lons)
            south, north = min(lats), max(lats)
            center_lat = (north + south) / 2
            center_lon = (west + east) / 2
            bounding_box_str = f"ENVELOPE({west},{east},{north},{south})"
            bbox_polygon = [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south]
            ]
        else:
            center_lat = ""
            center_lon = ""
            bounding_box_str = ""
            bbox_polygon = []

        # Build GeoJSON
        features = []
        if bbox_polygon:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [bbox_polygon]
                },
                "properties": {
                    "name": "Map Boundary",
                    "source": bounding_box_source,
                    "map_township_ranges_used": updated_map_township
                }
            })
        features.extend(water_features)

        geojson_data = {
            "type": "FeatureCollection",
            "features": features
        }

        # Upload the GeoJSON to GitHub
        geojson_file_name = os.path.splitext(image_name)[0] + ".geojson"
        gh_url = upload_to_github(
            gh_client,
            github_repo_name,
            geojson_file_name,
            json.dumps(geojson_data, indent=2),
            f"Add {geojson_file_name}"
        )

        # Build CSV row
        if county_str and water_feature_list:
            spatial_coverage = county_str + "; " + "; ".join(water_feature_list)
        elif county_str:
            spatial_coverage = county_str
        elif water_feature_list:
            spatial_coverage = "; ".join(water_feature_list)
        else:
            spatial_coverage = ""

        description_csv = ("This item includes: " + ", ".join(water_feature_list) + ".") if water_feature_list else ""

        csv_row = {
            "Title*": os.path.splitext(image_name)[0],
            "Alternate Title": "",
            "Creator*": "",
            "Contributor": "",
            "Artist": "",
            "Author": "",
            "Composer": "",
            "Editor": "",
            "Lyricist": "",
            "Producer": "",
            "Publisher": "",
            "Coverage": map_description,
            "Spatial Coverage": spatial_coverage,
            "Temporal Coverage": "",
            "Latitude": center_lat,
            "Longitude": center_lon,
            "Bounding Box": bounding_box_str,
            "External Reference": gh_url,
            "Advisor": "",
            "Committee Member": "",
            "Degree Name": "",
            "Degree Level": "",
            "Department": "",
            "University": "",
            "Date*": "",
            "Date Created": "",
            "Date Issued": "",
            "Date Recorded": "",
            "Date Submitted": "",
            "Date Search*": "",
            "Description": description_csv,
            "Abstract": "",
            "Award": "",
            "Frequency": "",
            "Sponsorship": "",
            "Table of Contents": "",
            "Subject": "",
            "LCSH Subject*": "",
            "Language": "",
            "Language-ISO": "",
            "Format": "",
            "Medium*": "",
            "Extent": "",
            "Type*": "",
            "Source": "",
            "Digital Collection*": "",
            "Physical Collection*": "",
            "Series/Location*": "",
            "Subcollection": "",
            "Repository*": "",
            "Rights*": "",
            "Rights Note": "",
            "Rights License": "",
            "Rights URI": "",
            "Rights DPLA*": "",
            "Identifier": "",
            "Citation": "",
            "DOI": "",
            "ISBN": "",
            "URI": "",
            "Related Resource*": "",
            "Relation-Has Format Of": "",
            "Relation-Has Part": "",
            "Relation-Has Version": "",
            "Relation-Is Format Of": "",
            "Relation-Is Referenced By": "",
            "Relation-Is Replaced By": "",
            "Relation-Is Version Of": "",
            "Relation-References": "",
            "Relation-Replaces": "",
            "Transcript": "",
            "Path": "",
            "File Name": image_name
        }
        

        # Append or create CSV in S3
        analysis_csv_key = f"{analysis_folder}/dublin core metadata analysis file.csv"
        try:
            existing_obj = s3_client.get_object(Bucket=bucket_name, Key=analysis_csv_key)
            existing_csv = existing_obj["Body"].read().decode("utf-8")
            df_existing = pd.read_csv(io.StringIO(existing_csv))
            df_new = pd.DataFrame([csv_row])
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        except s3_client.exceptions.NoSuchKey:
            df_combined = pd.DataFrame([csv_row])
        except Exception as e:
            print(f"Error reading existing CSV file: {e}. Creating a new one.")
            df_combined = pd.DataFrame([csv_row])

        csv_buffer = io.StringIO()
        df_combined.to_csv(csv_buffer, index=False)
        s3_client.put_object(
            Bucket=bucket_name,
            Key=analysis_csv_key,
            Body=csv_buffer.getvalue(),
            ContentType='text/csv'
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Analysis completed successfully.",
                "csv_location": f"s3://{bucket_name}/{analysis_csv_key}"
            })
        }

    except Exception as e:
        error_message = f"Error processing image '{object_key}': {str(e)}"
        print(error_message)
        error_file_name = f"{os.path.splitext(os.path.basename(object_key))[0]}.txt"
        s3_client.put_object(
            Bucket=bucket_name,
            Key=f"{error_folder}/{error_file_name}",
            Body=error_message
        )
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_message})
        }
