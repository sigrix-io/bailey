"""Bailey: an open-source AI assistant that runs ready-made solutions.

A solution is a complete, versioned package for one job: instructions, skills,
tools, knowledge and small apps. Bailey installs one in a step, runs it on the
user's own machine with the user's own AI key, and asks before a tool changes
anything.

This first release carries one command, ``bailey doctor``, which checks that a
machine has what the assistant will need. It reads the local machine only and
makes no network call.
"""

__version__ = "0.0.1"
