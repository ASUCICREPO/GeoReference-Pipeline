import aws_cdk as core
import aws_cdk.assertions as assertions

from geo_reference_pipeline.geo_reference_pipeline_stack import GeoReferencePipelineStack

# example tests. To run these tests, uncomment this file along with the example
# resource in geo_reference_pipeline/geo_reference_pipeline_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = GeoReferencePipelineStack(app, "geo-reference-pipeline")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
