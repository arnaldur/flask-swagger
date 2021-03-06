import os
import sys
import argparse
import json
import pkg_resources
from flask_swagger import swagger, quart_url_parser, flask_rule_parser, flask_url_parser

sys.path.append(os.getcwd())

parser = argparse.ArgumentParser()
parser.add_argument("app", help="the flask app to swaggerify")
parser.add_argument(
    "--template",
    help="template spec to start with, before any other options or processing",
)
parser.add_argument("--out-dir", default=None, help="the directory to output to")
parser.add_argument("--definitions", default=None, help="json definitions file")
parser.add_argument("--host", default=None)
parser.add_argument("--base-path", default=None)
parser.add_argument("--version", default=None, help="Specify a spec version")
parser.add_argument("--framework", default=None, help="Specify framework")
args = parser.parse_args()


def run():
    ep = pkg_resources.EntryPoint.parse("x=%s" % args.app)

    if hasattr(ep, "resolve"):
        app = ep.resolve()
    else:
        app = ep.load(False)

    # load the base template
    template = None
    if args.template is not None:
        with open(args.template, "r") as f:
            template = json.loads(f.read())

        # overwrite template with specified arguments
        if args.definitions is not None:
            with open(args.definitions, "r") as f:
                rawdefs = json.loads(f.read())
                if "definitions" in rawdefs:
                    rawdefs = rawdefs["definitions"]
                for d in rawdefs.keys():
                    template["definitions"][d] = rawdefs[d]

    rule_parser = flask_rule_parser
    url_parser = flask_url_parser
    if args.framework is not None:
        if args.framework == "quart":
            url_parser = quart_url_parser

    spec = swagger(
        app, template=template, url_parser=url_parser, rule_parser=rule_parser
    )

    if args.host is not None:
        spec["host"] = args.host
    if args.base_path is not None:
        spec["basePath"] = args.base_path
    if args.version is not None:
        spec["info"]["version"] = args.version
    if args.out_dir is None:
        print(json.dumps(spec, indent=4, sort_keys=True))
    else:
        with open("%s/swagger.json" % args.out_dir, "w") as f:
            f.write(json.dumps(spec, indent=4, sort_keys=True))
            f.close()
