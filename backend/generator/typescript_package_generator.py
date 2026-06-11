import json


class TypeScriptPackageGenerator:

    def generate_package_json(
        self,
        package_name: str
    ):

        package = {
            "name": package_name,
            "version": "1.0.0",
            "main": "dist/index.js",
            "types": "dist/index.d.ts",
            "scripts": {
                "build": "tsc"
            }
        }

        return json.dumps(
            package,
            indent=4
        )

    def generate_tsconfig(
        self
    ):

        config = {
            "compilerOptions": {
                "target": "ES2020",
                "module": "ESNext",
                "declaration": True,
                "outDir": "dist",
                "strict": True
            }
        }

        return json.dumps(
            config,
            indent=4
        )
