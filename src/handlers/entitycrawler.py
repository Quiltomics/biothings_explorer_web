#import requests
import asyncio
from aiohttp import ClientSession
import time
from collections import defaultdict
import json
from timeit import default_timer as timer
#import requests_cache
#from joblib import Parallel, delayed
#import multiprocessing

from biothings_explorer import BioThingsExplorer
from biothings_explorer.jsonld_processor import JSONLDHelper
from biothings_explorer.utils import property_uri_2_prefix_dict
from biothings_explorer.output_organizer import OutputOrganizor
from biothings_explorer.id_converter import IDConverter
from .basehandler import BaseHandler
import logging
import os,sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from config import crawler_log_file

logger = logging.getLogger('entitycrawler')
logger.setLevel(logging.DEBUG)
logger_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger_handler = logging.FileHandler(crawler_log_file)
logger_handler.setLevel(logging.DEBUG)
logger_handler.setFormatter(logger_formatter)
logger.addHandler(logger_handler)

#requests_cache.install_cache('biothings_cache', backend='sqlite', expire_after=36000)
bt_explorer = BioThingsExplorer()
jh = JSONLDHelper()
oo = OutputOrganizor()

def find_endpoint(input_type):
    """
    This function takes input_type, e.g. ncbigene as input, and return all endpoints which can ingest the input_type

    Return
    ======
    List of endpoints
    """
    return list(bt_explorer.api_map.successors(input_type))

async def get_json_helper(_endpoint, input_type, input_value, session):
    api_call_params = bt_explorer.apiCallHandler.call_api({input_type: input_value}, _endpoint)
    try:
        start = time.time()
        logger.info('Start making API calls to {}'.format(_endpoint))
        async with session.get(api_call_params[0], params=api_call_params[1], headers={'Accept': 'application/json'}) as response:
            json_response = await response.json()
            data = bt_explorer.apiCallHandler.preprocess_json_doc(json_response, _endpoint)
            logger.info('Finished making API calls to %s, took %s', _endpoint, time.time() - start)
            return {'endpoint': _endpoint, 'data': data}
    except:
        return {'endpoint': _endpoint, 'data': {}}
    #response = requests.get(params[0], params=params[1], headers={'Accept': 'application/json'})
    #if response.status_code == 200:
    #logger.info('Start get json output from {}'.format(_endpoint))
    

async def get_json(paths):
    """
    Given paths, with each path consisting of (endpoint, input_type, input_value),
    Make API calls for each pair of (endpoint, input_type, input_value),
    Return JSON output from the API call

    Return
    ======
    List of (endpoint, JSON output)
    """
    start = time.time()
    async with ClientSession() as session:
        tasks = [asyncio.ensure_future(get_json_helper(_path[0], _path[1], _path[2], session)) for _path in paths]
        results = await asyncio.gather(*tasks)
        logger.info("Fetching json docs took: {:.2f} seconds".format(time.time() - start))
    return results
    """
    # construct API calls for each endpoint, organize them into a list
    api_call_params = []
    for endpoint_name in endpoints:
        api_call_params.append(bt_explorer.apiCallHandler.call_api({input_type: input_value}, endpoint_name))
    # use grequest to make asynchronized API calls
    rs = (grequests.get(u, params=v, headers={'Accept': 'application/json'}) for (u,v) in api_call_params)
    # get JSON output
    responses = [bt_explorer.apiCallHandler.preprocess_json_doc(api_call_response.json(), endpoint_name)
    if api_call_response.status_code == 200 else {} for api_call_response in grequests.map(rs)]
    """
    """
    # multiprocessing solution
    num_cores = multiprocessing.cpu_count()
    results = Parallel(n_jobs=num_cores)(delayed(get_json_helper)(_endpoint, input_type, input_value) for _endpoint in endpoints)
    return list(zip(endpoints, results))
    """

def uri2curie(URI):
    """
    Given an URI, e.g. http://identifiers.org/ncbigene/1017
    Return it in CURIE format, e.g. NCBIGene:1017

    Return
    ======
    CURIE
    """
    _value = URI.split('/')[-1]
    _uri = URI[:len(URI)-len(_value)]
    if _uri in bt_explorer.registry.bioentity_info:
        prefix = bt_explorer.registry.bioentity_info[_uri]['prefix']
        return (prefix + ':' + _value)
    else:
        return _value

def extractnquads(nquads):
    """
    Given an nquads doc
    Extract the predicate and object info
    For object URI, convert it into CURIE format, e.g. http://identifiers.org/ncbigene/1017 --> NCBIGene:1017
    For predicate, convert it from URI to normal text, e.g. http://biothings.io/ontology/targets --> targets

    Return
    ======
    List of (CURIE, RELATION)
    """
    if not nquads:
        return None
    if "@default" not in nquads:
        return None
    # Loop through each nquad
    pairs = []
    for _nquad in nquads["@default"]:
        if not _nquad['object']['value'].startswith('_:'):
            _object = _nquad['object']['value']
            _predicate = _nquad['predicate']['value']
            curie = uri2curie(_object)
            if curie:
                pairs.append({'curie': curie, 'predicate': _predicate.split('/')[-1]})
    return pairs


def exploreinput(input_type, input_value):
    """
    Main function
    Takes an input_type, input_value,
    crawling all API endpoints taking the input
    Return all bio-entities related to this input

    Return
    ======
    List of (CURIE, RELATION, Endpoint, API)
    """
    ###################################################################
    """
    This part find all synonyms of the input, and endpoints which can take them
    """
    logger.info('Crawler takes input %s:%s', input_type, input_value)
    paths = []
    endpoints_set = set()
    # find synonyms
    synonyms = IDConverter().find_synonym(input_value, input_type)
    # find endpoints which can take synonyms
    for _prefix, _value in synonyms[0].items():
        endpoints = find_endpoint(_prefix)
        # loop through each endpoint
        for _endpoint in endpoints:
            # make sure endpoint is unique
            if _endpoint not in endpoints_set:
                endpoints_set.add(_endpoint)
                # loop through each synonym of same prefix
                if type(_value) == list:
                    for _item in _value:
                        paths.append([_endpoint, bt_explorer.registry.prefix2uri(_prefix), _item])
                else:
                    paths.append([_endpoint, bt_explorer.registry.prefix2uri(_prefix), _value])
    """
    This part use asyncio libary to asynchronously extract
    all the JSON outputs from individual API calls
    """
    outputs = defaultdict(list)
    if paths:
        logger.info('Paths found for this input: %s', paths)
        ioloop = asyncio.get_event_loop()
        json_docs = ioloop.run_until_complete(get_json(paths))
        ###################################################################
        """
        This part add JSON-LD context file to individual JSON document
        """
        start = time.time()
        jsonld_docs = []
        for json_doc in json_docs:
            endpoint_name = json_doc['endpoint']
            if 'jsonld_context' in bt_explorer.registry.endpoint_info[endpoint_name]:
                jsonld_context_path = bt_explorer.registry.endpoint_info[endpoint_name]['jsonld_context']
                jsonld_docs.append(jh.json2jsonld(json_doc['data'], jsonld_context_path))
            else:
                jsonld_docs.append(None)
        logger.info("Converting json docs to jsonld docs took: {:.2f} seconds".format(time.time() - start))

        ###################################################################
        """
        This part convert JSON-LD document to Nquads format
        And extract and organize the data
        """
        # rearrange the endpoints, because Asyncio will reorder the sequence
        start = time.time()
        endpoints = [_json['endpoint'] for _json in json_docs]
        # convert a list of jsonld documents to nquads documents
        # this is the slowest step in the process
        nquads_list = jh.jsonld2nquads(jsonld_docs, alwayslist=True)
        logger.info("Converting jsonld docs to nquads docs took: {:.2f} seconds".format(time.time() - start))
        start = time.time()
        for endpoint, nquads in list(zip(endpoints, nquads_list)):
            # get all possible associations of the endpoint
            association_list = bt_explorer.registry.endpoint_info[endpoint]['associations']
            if "@default" in nquads:
                _output = jh.fetch_properties_by_association_in_nquads(nquads, association_list)
                for _assoc, _objects in _output.items():
                    for _object in _objects:
                        reorganized_data = {'api': bt_explorer.registry.endpoint_info[endpoint]['api'],
                                            'endpoint': endpoint,
                                            'predicate': _assoc.replace('http://biothings.io/explorer/vocab/objects/', '')}
                        reorganized_data.update(oo.nquads2dict(_object))
                        object_id_prefix = reorganized_data['object']['id'].split(':')[0]
                        reorganized_data.update({'prefix': object_id_prefix})
                        object_semantic_type = bt_explorer.registry.prefix2semantictype(object_id_prefix.lower())
                        outputs[object_semantic_type].append(reorganized_data)
        logger.info("Organizing nquads outputs took: {:.2f} seconds".format(time.time() - start))

        ###################################################################

        """
        property_summary = defaultdict(set)
        semantic_type_summary = defaultdict(set)
        summary = {}
        for semantic_type, pair in outputs.items():
            summary[semantic_type] = defaultdict(set)
            for _doc in pair:
                for _property, _value in _doc.items():
                    if _property == 'prefix':
                        summary[semantic_type][_property].add(_value)
                    elif _property in ['relation.label', 'relation.id', 'publication', 'evidence', 'object.taxonomy']:
                        if type(_value) != list:
                            if ':' in _value:
                                _prefix = _value.split(':')[0]
                                _value_no_prefix = _value[len(_prefix)+1:]
                                summary[semantic_type][_prefix].add(_value_no_prefix)
                            else:
                                summary[semantic_type][_property].add(_value)
                        else:
                            for _single_value in _value:
                                if ':' in _single_value:
                                    _prefix = _single_value.split(':')[0]
                                    _value_no_prefix = _single_value[len(_prefix)+1:]
                                    summary[semantic_type][_prefix].add(_value_no_prefix)
                                else:
                                    summary[semantic_type][_property].add(_single_value)
                    elif _property not in ['object.secondary-id', 'object.label']:
                        if type(_value) != list:
                            summary[semantic_type][_property].add(_value)
                        else:
                            for _single_value in _value:
                                summary[semantic_type][_property].add(_single_value)
        for k,v in summary.items():
            for _k, _v in v.items():
                summary[k][_k] = list(_v)
        """
    else:
        logger.info("couldn't find any path for this input!")
    return {'linkedData': outputs}
    """
        if _output:
            for _pair in _output:
                semantic_type = bt_explorer.registry.prefix2semantictype(_pair['curie'].split(':')[0])
                _pair.update({'endpoint': endpoint, 'api': bt_explorer.registry.endpoint_info[endpoint]['api'], 'prefix': _pair['curie'].split(':')[0]})
                outputs[semantic_type].append(_pair)
    summary = {}
    start = timer()
    for semantic_type, pair in outputs.items():
        summary[semantic_type] = defaultdict(set)
        for _doc in pair:
            summary[semantic_type]['api'].add(_doc['api'])
            summary[semantic_type]['endpoint'].add(_doc['endpoint'])
            summary[semantic_type]['predicate'].add(_doc['predicate'])
            summary[semantic_type]['id'].add(_doc['curie'].split(':')[0])
    for k,v in summary.items():
        for _k, _v in v.items():
            summary[k][_k] = list(_v)
    end = timer()
    print('Amount of time used for organizing summary is : {}'.format(end - start))
    results = {'summary': summary, 'linkedData': outputs}
    return results
    """
class Crawler(BaseHandler):
    """
    This function serves as one BioThings Explorer API endpoint
    Given an input_type and input_value,
    return all biological entities(type & value) which could be linked to this entity

    Params
    ======
    DefaultDict grouped by semantic type

    """
    def get(self):
        input_type = self.get_query_argument('input_type')
        input_value = self.get_query_argument('input_value')
        output_summary = self.get_query_argument('summary', False)
        results = exploreinput(input_type, input_value)
        if results:
            if output_summary:
                self.write(json.dumps(results))
            else:
                self.write(json.dumps({'linkedData': results['linkedData']}))
        else:
            self.set_status(400)
            self.write(json.dumps({"status": 400, "error": "No linked data could be found for " + input_type + ":" + input_value + '!'}))
