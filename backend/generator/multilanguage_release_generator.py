from dataclasses import dataclass


@dataclass
class MultiLanguageRelease:

    python_bundle: object

    typescript_bundle: object

    manifest: dict

    metadata: dict


class MultiLanguageReleaseGenerator:

    def generate_release(
        self,
        python_bundle,
        typescript_bundle
    ):

        manifest = {

            "languages": [
                "python",
                "typescript"
            ],

            "artifacts": {

                "python":
                    python_bundle[
                        "manifest"
                    ],

                "typescript":
                    typescript_bundle[
                        "manifest"
                    ]
            }
        }

        metadata = {

            "release_version":
                "1.0.0",

            "sdk_count":
                2
        }

        return MultiLanguageRelease(
            python_bundle=
                python_bundle,

            typescript_bundle=
                typescript_bundle,

            manifest=
                manifest,

            metadata=
                metadata
        )
