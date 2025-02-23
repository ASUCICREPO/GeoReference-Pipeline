# 📌 GeoReference Pipeline

## 🚀 Overview
The **GeoReference Pipeline** is a cloud-native, fully automated system designed to process and analyze geospatial map images efficiently. This system enables compression of raw TIFF maps into optimized PNG formats, extracts meaningful metadata using **Amazon Bedrock LLMs**, and integrates with **GitHub** for geospatial data storage.

## 📊 Key Features
- **AWS Lambda & S3 Triggers**: Handles automated processing of map files from S3 storage.
- **PIL-Based Image Compression**: Reduces TIFF file sizes while maintaining quality.
- **AWS Bedrock Claude 3.5 Integration**: Extracts map metadata using Large Language Models (LLMs).
- **Automated GitHub Storage**: Stores processed GeoJSON outputs in a GitHub repository.
- **Error Handling & CloudWatch Logging**: Ensures robust monitoring and debugging.

## 🏗️ Architecture Overview

1. **Upload TIFF Map to S3 (`raw/` folder)**
2. **Compression Lambda Converts TIFF to PNG**
3. **Compressed Images Stored in `compressed/` Folder**
4. **Analysis Lambda Extracts Metadata Using AWS Bedrock**
5. **GeoJSON & CSV Metadata Files Generated**
6. **GeoJSON Data Pushed to GitHub Repository**
7. **Error Handling & Logging in `error/` Folder**

## 🛠️ Setup & Installation

### Prerequisites 🔑
Ensure the following are installed and configured:
- **AWS CLI** (with IAM permissions)
- **AWS CDK** (globally installed)
- **Docker** (running)
- **GitHub Token** (for repository access)

### Step 1: Clone the Project 🧑‍💻
```sh
$ git clone https://github.com/YOUR_GITHUB_USERNAME/water_resources_geojson.git
$ cd water_resources_geojson
```

### Step 2: Configure AWS CDK Environment ⚙️
**Bootstrap AWS CDK (First-Time Setup)**
```sh
$ cdk bootstrap
```

### Step 3: Set Project Variables in `cdk.json`
Modify `cdk.json` to include:
```json
"context": {
    "bucket_name": "my-geo-pipeline-bucket",
    "compression_function_name": "GeoCompressionLambda",
    "analysis_function_name": "GeoAnalysisLambda",
    "compression_layer_name": "GeoCompressionLayer",
    "analysis_layer_name": "GeoAnalysisLayer",
    "github_token": "YOUR_GITHUB_ACCESS_TOKEN",
    "github_repo_name": "water_resources_geojson",
    "bedrock_model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "bedrock_region": "us-west-2",
    "max_lambda_memory_mb": 10240,
    "max_lambda_timeout_minutes": 15,
    "max_lambda_ephemeral_storage_mb": 10240,
    "compression_target_mb": 3,
    "prompt_file_name": "prompt.py"
}
```

### Step 4: Deploy the AWS CDK Stack 🚀
```sh
$ cdk deploy --all
```

Once the deployment is complete, the necessary AWS services will be created, including:
- **S3 Buckets** (`raw/`, `compressed/`, `error/`, `analysis/`)
- **Lambda Functions** (Compression & Analysis)
- **IAM Roles & Policies**
- **AWS Bedrock Model Integration**
- **GitHub Integration for GeoJSON Files**

## 📤 Upload & Test the Pipeline
### Step 5: Upload a Test File to S3
```sh
$ aws s3 cp test-map.tif s3://my-geo-pipeline-bucket/raw/
```

### Step 6: Monitor Processing in AWS CloudWatch
To check logs for the Compression Lambda:
```sh
$ aws logs tail /aws/lambda/GeoCompressionLambda --follow
```

To check logs for the Analysis Lambda:
```sh
$ aws logs tail /aws/lambda/GeoAnalysisLambda --follow
```

### Step 7: Verify Outputs
- **Check the `compressed/` folder** for the converted PNG.
- **Check the `analysis/` folder** for the generated CSV metadata.
- **Verify the GitHub Repository** for the stored GeoJSON file.
- **Check the `error/` folder** if any errors occur during processing.

## 🔄 Troubleshooting
### Issue: Lambda Function Errors
Check logs in CloudWatch:
```sh
$ aws logs tail /aws/lambda/GeoCompressionLambda --follow
$ aws logs tail /aws/lambda/GeoAnalysisLambda --follow
```

### Issue: GitHub Upload Fails
Ensure your GitHub Token is correct in `cdk.json` and has `repo` access.

### Issue: S3 File Not Triggering Lambda
Make sure S3 notifications are enabled:
```sh
$ aws s3api get-bucket-notification-configuration --bucket my-geo-pipeline-bucket
```

## 🎯 Conclusion
This **GeoReference Pipeline** provides a scalable, cloud-native solution for processing and analyzing geospatial maps. It automates compression, metadata extraction, and structured data storage while leveraging AWS services for seamless execution.

Happy coding! 🚀
