# from aws_cdk import (
#     # Duration,
#     Stack,
#     # aws_sqs as sqs,
# )
# from constructs import Construct

# class GeoReferencePipelineStack(Stack):

#     def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
#         super().__init__(scope, construct_id, **kwargs)

#         # The code that defines your stack goes here

#         # example resource
#         # queue = sqs.Queue(
#         #     self, "GeoReferencePipelineQueue",
#         #     visibility_timeout=Duration.seconds(300),
#         # )

import os
from aws_cdk import (
    Stack,
    RemovalPolicy,
    Duration,
    Size,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_s3_notifications as s3n,
    CfnOutput
)
from constructs import Construct


class GeoReferencePipelineStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ----------------------------------------------------
        # 1. Retrieve context variables
        # ----------------------------------------------------
        bucket_name = self.node.try_get_context("bucket_name") or "my-geo-pipeline-bucket"
        
        # Lambda function names
        compression_fn_name = self.node.try_get_context("compression_function_name") or "GeoCompressionLambda"
        analysis_fn_name = self.node.try_get_context("analysis_function_name") or "GeoAnalysisLambda"

        # Layer names
        compression_layer_name = self.node.try_get_context("compression_layer_name") or "GeoCompressionLayer"
        analysis_layer_name = self.node.try_get_context("analysis_layer_name") or "GeoAnalysisLayer"

        # GitHub
        github_token = self.node.try_get_context("github_token") or "YOUR_GITHUB_TOKEN_HERE"
        github_repo_name = self.node.try_get_context("github_repo_name") or "water_resources_geojson"

        # Bedrock
        bedrock_model_id = self.node.try_get_context("bedrock_model_id") or "anthropic.claude-3-5"
        bedrock_region = self.node.try_get_context("bedrock_region") or "us-west-2"

        # Additional settings (Lambda memory, ephemeral storage, etc.)
        max_lambda_mem = int(self.node.try_get_context("max_lambda_memory_mb") or 1024)
        max_lambda_timeout = int(self.node.try_get_context("max_lambda_timeout_minutes") or 15)
        max_lambda_storage = int(self.node.try_get_context("max_lambda_ephemeral_storage_mb") or 1024)
        
        # Compression-specific
        compression_target_mb = int(self.node.try_get_context("compression_target_mb") or 3)

        # Optionally, if you want to pass the prompt file name as an env variable
        prompt_file_name = self.node.try_get_context("prompt_file_name") or "prompt.py"

        # ----------------------------------------------------
        # 2. Create S3 bucket
        # ----------------------------------------------------
        data_bucket = s3.Bucket(
            self,
            "GeoDataBucket",
            bucket_name=bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # Create subfolders by deploying placeholder files (optional, but nice for initial structure)
        folders = ["raw/", "compressed/", "error/", "analysis/"]
        for folder in folders:
            s3_deployment.BucketDeployment(
                self,
                f"Create{folder.capitalize().replace('/', '')}Folder",
                destination_bucket=data_bucket,
                destination_key_prefix=folder,
                sources=[s3_deployment.Source.data("placeholder.txt", "Placeholder")],
                retain_on_delete=False
            )

        # ----------------------------------------------------
        # 3. Create Lambda Layers (from local .zip files)
        # ----------------------------------------------------
    
        compression_layer = _lambda.LayerVersion(
            self,
            compression_layer_name,
            layer_version_name=compression_layer_name,
            description="Layer for image compression dependencies",
            code=_lambda.Code.from_asset("geo_reference_pipeline/layers/layer1.zip"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13]
        )

        analysis_layer = _lambda.LayerVersion(
            self,
            analysis_layer_name,
            layer_version_name=analysis_layer_name,
            description="Layer for analysis dependencies",
            code=_lambda.Code.from_asset("geo_reference_pipeline/layers/layer2.zip"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13]
        )

        # ----------------------------------------------------
        # 4. IAM Role for Lambdas & Policies
        # ----------------------------------------------------
        lambda_role = iam.Role(
            self,
            "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )
        
        # Basic Execution (CloudWatch Logs, etc.)
        lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )

        # S3 access
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"],
                resources=[data_bucket.bucket_arn, f"{data_bucket.bucket_arn}/*"]
            )
        )

        # Bedrock permissions (to invoke model)
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"]  # Or specify your bedrock resources if you prefer
            )
        )

        # (Optionally) VPC, Secrets, or other permissions as needed for your environment.

        # ----------------------------------------------------
        # 5. Define the Compression Lambda (trigger on raw/)
        # ----------------------------------------------------
        compression_lambda = _lambda.Function(
            self,
            "CompressionLambda",
            function_name=compression_fn_name,
            runtime=_lambda.Runtime.PYTHON_3_13,
            role=lambda_role,
            handler="compression_handler.lambda_handler",
            code=_lambda.Code.from_asset("geo_reference_pipeline/lambda_functions/compress_lambda"),
            memory_size=max_lambda_mem,
            timeout=Duration.minutes(max_lambda_timeout),
            ephemeral_storage_size=Size.mebibytes(max_lambda_storage),
            layers=[compression_layer],
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
                "COMPRESSION_TARGET_MB": str(compression_target_mb),
                "ERROR_FOLDER": "error",
                "COMPRESSED_FOLDER": "compressed"
            }
        )

        # Create S3 notification for raw/ folder
        notification_raw = s3n.LambdaDestination(compression_lambda)
        data_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            notification_raw,
            s3.NotificationKeyFilter(prefix="raw/")
        )

        # ----------------------------------------------------
        # 6. Define the Analysis Lambda (trigger on compressed/)
        # ----------------------------------------------------
        analysis_lambda = _lambda.Function(
            self,
            "AnalysisLambda",
            function_name=analysis_fn_name,
            runtime=_lambda.Runtime.PYTHON_3_13,
            role=lambda_role,
            handler="analysis_handler.lambda_handler",
            code=_lambda.Code.from_asset("geo_reference_pipeline/lambda_functions/analysis_lambda"),
            memory_size=max_lambda_mem,
            timeout=Duration.minutes(max_lambda_timeout),
            ephemeral_storage_size=Size.mebibytes(max_lambda_storage),
            layers=[analysis_layer],
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
                "ERROR_FOLDER": "error",
                "ANALYSIS_FOLDER": "analysis",
                "GITHUB_TOKEN": github_token,
                "GITHUB_REPO_NAME": github_repo_name,
                "BEDROCK_MODEL_ID": bedrock_model_id,
                "BEDROCK_REGION": bedrock_region,
                "PROMPT_FILE_NAME": prompt_file_name
            }
        )

        # Create S3 notification for compressed/ folder
        notification_compressed = s3n.LambdaDestination(analysis_lambda)
        data_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            notification_compressed,
            s3.NotificationKeyFilter(prefix="compressed/")
        )

        # ----------------------------------------------------
        # 7. Outputs
        # ----------------------------------------------------
        CfnOutput(self, "BucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "CompressionLambdaName", value=compression_lambda.function_name)
        CfnOutput(self, "AnalysisLambdaName", value=analysis_lambda.function_name)
