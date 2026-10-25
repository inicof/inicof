"""Run the local development app; never expose this server publicly."""

import argparse
from .web import create_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=5055)
args = parser.parse_args()
create_app().run(host="127.0.0.1", port=args.port, debug=False)
