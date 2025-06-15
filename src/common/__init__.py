import os


AGENT_BASEDIR = os.path.sep + os.path.join(*os.getcwd().split(os.path.sep)[:-3])
MCP_BASEDIR = f'{AGENT_BASEDIR}/mcps'
