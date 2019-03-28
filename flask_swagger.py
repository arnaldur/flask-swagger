"""
What's the big idea?

An endpoint that traverses all restful endpoints producing a swagger 2.0 schema
If a swagger yaml description is found in the docstrings for an endpoint
we add the endpoint to swagger specification output

"""
import inspect
import yaml
import re

from collections import defaultdict


IGNORE_VERBS = {"HEAD", "OPTIONS"}
# technically only responses is non-optional
OPTIONAL_FIELDS = ['tags', 'consumes', 'produces', 'schemes', 'security',
                   'deprecated', 'operationId', 'externalDocs']


def _sanitize(comment):
    return comment.replace('\n', '<br/>') if comment else comment


def _find_from_file(full_doc, from_file_keyword):
    """
    Finds a line in <full_doc> like

        <from_file_keyword> <colon> <path>

    and return path
    """
    path = None

    for line in full_doc.splitlines():
        if from_file_keyword in line:
            parts = line.strip().split(':')
            if len(parts) == 2 and parts[0].strip() == from_file_keyword:
                path = parts[1].strip()
                break

    return path


def _doc_from_file(path):
    doc = None
    with open(path) as f:
        doc = f.read()
    return doc


def _definition_from_jsonschema(module, name):
    """Resolve the definition if referenced as import."""

    ref = __import__(module, globals(), locals(), [name], 0)
    return getattr(ref, name)


def _parse_docstring(obj, process_doc, from_file_keyword):
    first_line, other_lines, swag = None, None, None
    full_doc = inspect.getdoc(obj)
    if full_doc:
        if from_file_keyword is not None:
            from_file = _find_from_file(full_doc, from_file_keyword)
            if from_file:
                full_doc_from_file = _doc_from_file(from_file)
                if full_doc_from_file:
                    full_doc = full_doc_from_file
        line_feed = full_doc.find('\n')
        if line_feed != -1:
            first_line = process_doc(full_doc[:line_feed])
            yaml_sep = full_doc[line_feed + 1:].find('---')
            if yaml_sep != -1:
                other_lines = process_doc(
                    full_doc[line_feed + 1:line_feed + yaml_sep]
                )
                swag = yaml.load(full_doc[line_feed + yaml_sep:], Loader=yaml.FullLoader)
            else:
                other_lines = process_doc(full_doc[line_feed + 1:])
        else:
            first_line = full_doc
    return first_line, other_lines, swag


def _extract_definitions(alist, level=None):
    """
    Since we couldn't be bothered to register models elsewhere
    our definitions need to be extracted from the parameters.
    We require an 'id' field for the schema to be correctly
    added to the definitions list.
    """

    def _extract_array_defs(source):
        # extract any definitions that are within arrays
        # this occurs recursively
        ret = []
        items = source.get('items')
        if items is not None and 'schema' in items:
            ret += _extract_definitions([items], level + 1)
        return ret

    # for tracking level of recursion
    if level is None:
        level = 0

    defs = list()
    if alist is not None:
        for item in alist:
            schema = item.get("schema")
            if (schema is not None and
                    schema.get("$ref") is not None and
                    "import/" in schema.get("$ref")):
                ref = schema.get("$ref")
                # resolve local reference to python object
                # "#/import/timeline.models.user.UserObject"
                # process the import line
                module_path = ref.split("import/")[1]
                parts = module_path.split('.')
                module = '.'.join(parts[:-1])
                name = parts[-1]

                # Point the ref to definitions
                schema['$ref'] = "#/definitions/{}".format(module_path)

                # Get the definition
                definition = _definition_from_jsonschema(module, name)
                # overrwrite the id with something good
                definition['id'] = module_path

                return [definition]
            if schema is not None:
                schema_id = schema.get("id")
                if schema_id is not None:
                    defs.append(schema)
                    ref = {"$ref": "#/definitions/{}".format(schema_id)}

                    # only add the reference as a schema if we are in a
                    # response or a parameter i.e. at the top level
                    # directly ref if a definition is used within another
                    # definition
                    if level == 0:
                        item['schema'] = ref
                    else:
                        item.update(ref)
                        del item['schema']

                # extract any definitions that are within properties
                # this occurs recursively
                properties = schema.get('properties')
                if properties is not None:
                    defs += _extract_definitions(properties.values(),
                                                 level + 1)

                defs += _extract_array_defs(schema)

            defs += _extract_array_defs(item)

    return defs


def flask_url_parser(app, prefix=None):
    """
    Walk the url definitions in the app and extract all functions
    handling urls.
    """
    urldict = defaultdict(list)
    for rule in app.url_map.iter_rules():
        url = str(rule)
        if prefix and rule.rule[:len(prefix)] != prefix:
            continue
        endpoint = app.view_functions[rule.endpoint]
        for verb in rule.methods.difference(IGNORE_VERBS):
            verb = verb.lower()
            if hasattr(endpoint, 'methods') \
                    and verb in map(lambda m: m.lower(), endpoint.methods) \
                    and hasattr(endpoint.view_class, verb):
                urldict[url].append((verb, getattr(endpoint.view_class, verb)))
            else:
                urldict[url].append((verb, endpoint))

    return urldict


def flask_rule_parser(rule):
    """
    Parse the flask url params to swagger spec urls
    """
    rule = str(rule)
    for arg in re.findall('(<([^<>]*:)?([^<>]*)>)', rule):
        rule = rule.replace(arg[0], '{%s}' % arg[2])
    return rule


def swagger(app,
            url_parser=flask_url_parser,
            rule_parser=flask_rule_parser,
            process_doc=_sanitize,
            prefix=None,
            from_file_keyword=None,
            template=None):
    """
    Call this from an @app.route method like this
    @app.route('/spec.json')
    def spec():
       return jsonify(swagger(app))

    We go through all endpoints of the app searching for swagger endpoints
    We provide the minimum required data according to swagger specs
    Callers can and should add and override at will

    Arguments:
    app -- the flask app to inspect

    Keyword arguments:
    process_doc -- text sanitization method, the default simply
                   replaces \n with <br>
    from_file_keyword -- how to specify a file to load doc from
    template -- The spec to start with and update as flask-swagger finds paths.
    """
    output = {
        "swagger": "2.0",
        "info": {
            "version": "0.0.0",
            "title": "Cool product name",
        }
    }

    paths = defaultdict(dict)
    definitions = defaultdict(dict)
    if template is not None:
        output.update(template)
        # check for template provided paths and definitions
        for k, v in output.get('paths', {}).items():
            paths[k] = v
        for k, v in output.get('definitions', {}).items():
            definitions[k] = v
    output["paths"] = paths
    output["definitions"] = definitions

    swagger_endpoints = url_parser(app, prefix)
    for rule, methods in swagger_endpoints.items():
        rule = rule_parser(rule)

        operations = dict()
        for verb, method in methods:
            summary, description, swag = _parse_docstring(method,
                                                          process_doc,
                                                          from_file_keyword)
            # Do we have docstring to parse?
            if swag is not None:
                defs = swag.get('definitions', [])
                defs = _extract_definitions(defs)
                params = swag.get('parameters', [])
                defs += _extract_definitions(params)
                responses = swag.get('responses', {})
                responses = {
                    str(key): value
                    for key, value in responses.items()
                }
                if responses is not None:
                    defs = defs + _extract_definitions(responses.values())
                for definition in defs:
                    def_id = definition.pop('id', None)
                    if def_id is not None:
                        definitions[def_id].update(definition)
                operation = dict(
                    summary=summary,
                    description=description,
                    responses=responses
                )
                # parameters - swagger ui dislikes empty parameter lists
                if len(params) > 0:
                    operation['parameters'] = params
                # other optionals
                for key in OPTIONAL_FIELDS:
                    if key in swag:
                        operation[key] = swag.get(key)
                operations[verb] = operation

        if len(operations):
            paths[rule].update(operations)
    return output
