"""Bailey: an open-source AI assistant that runs ready-made solutions.

A solution is a complete, versioned package for one job: instructions, skills,
tools, knowledge and small apps. Bailey installs one in a step, runs it on the
user's own machine with the user's own AI key, and asks before a tool changes
anything.

This first release carries one command, ``bailey doctor``, which checks that a
machine has what the assistant will need. It reads the local machine only and
makes no network call.
"""

import os as _os

__version__ = "0.1.3"

#: The switches that turn off the telemetry a library Bailey loads sends from native code, where no
#: Python audit hook sees it. They are set here, before anything imports such a library, and handed
#: to each app's runner (``bailey.apps.runner_environment``). onnxruntime, which document search and
#: MarkItDown's file detection run on, otherwise tries to reach Microsoft's telemetry collector once
#: it has been loaded for about ten seconds.
NATIVE_TELEMETRY_OFF = {"ORT_DISABLE_TELEMETRY": "1"}

_os.environ.update(NATIVE_TELEMETRY_OFF)
