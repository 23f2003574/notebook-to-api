from .pipeline_metadata import (
    PipelineMetadata
)


class OpenAPISchemaGenerator:

    def generate_schema(
        self,
        metadata: PipelineMetadata
    ):

        return {
            "endpoint":
                metadata.endpoint_name,

            "request": {
                field.name: {
                    "type":
                        field.field_type
                }
                for field
                in metadata.inputs
            },

            "response": {
                field.name: {
                    "type":
                        field.field_type
                }
                for field
                in metadata.outputs
            }
        }
