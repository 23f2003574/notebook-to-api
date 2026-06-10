class ExecutionHooks:

    def before_pipeline(
        self,
        runtime
    ):
        pass

    def after_pipeline(
        self,
        runtime
    ):
        pass

    def before_stage(
        self,
        stage_name,
        runtime
    ):
        pass

    def after_stage(
        self,
        stage_name,
        runtime,
        result
    ):
        pass

    def on_stage_failure(
        self,
        stage_name,
        runtime,
        exception
    ):
        pass