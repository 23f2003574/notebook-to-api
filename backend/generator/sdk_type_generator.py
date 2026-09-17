from .pipeline_metadata import (
    PipelineMetadata
)


class SDKTypeGenerator:

    def generate_types(
        self,
        metadata: PipelineMetadata
    ):

        return {
            "request_types": {
                field.name:
                    field.field_type
                for field
                in metadata.inputs
            },

            "response_types": {
                field.name:
                    field.field_type
                for field
                in metadata.outputs
            }
        }
