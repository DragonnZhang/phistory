"""Bootstrap cell: use the initial task or prompt, then ask the model to choose the next cell."""

task = " ".join(globals().get("argv", [])) or input("Your task: ")
call_me(input=task)  # noqa: F821 -- core binds call_me.
