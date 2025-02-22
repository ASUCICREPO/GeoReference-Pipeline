PROMPT = """
Analyze the uploaded map image thoroughly to identify all relevant details. Then, 
return a strictly formatted JSON object with the exact structure and keys below 
(and nothing else):

{
  "map_description": "string",
  "township_range": [],
  "county": "string",
  "water_resources": []
}

Instructions:
1. ...
2. ...
(etc.)
"""
