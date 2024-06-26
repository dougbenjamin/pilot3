#!/usr/bin/env python
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# Authors:
# - Wen Guan, wen.guan@cern.ch, 2018
# - Paul Nilsson, paul.nilsson@cern.ch, 2023-24

"""Main classes to manage the messages between ES and harvester/ACT/Panda."""

import json
import logging
import os
import threading
import time
import queue
from typing import Any

from pilot.common import exception
from pilot.common.pluginfactory import PluginFactory

logger = logging.getLogger(__name__)


class CommunicationResponse:
    """Communication response class."""

    exception = None

    def __init__(self, attrs: dict = None):
        """
        Initialize variables.

        :param attrs: attributes dictionary (dict).
        """
        if not attrs:
            attrs = {}
        if not isinstance(attrs, dict):
            attrs = json.loads(attrs)

        def_attrs = {'status': None, 'content': None, 'exception': None}
        for key, value in def_attrs.items():
            if key not in attrs:
                attrs[key] = value

        for key, value in attrs.items():
            setattr(self, key, value)

    def __str__(self) -> str:
        """
        Return string representation.

        :return: string representation (str).
        """
        json_str = {}
        for key, value in list(self.__dict__.items()):
            if value and isinstance(value, list):
                json_str[key] = []
                for list_item in value:
                    json_str[key].append(str(list_item))
            elif value:
                json_str[key] = str(value)
            else:
                json_str[key] = value
        return json.dumps(json_str)


class CommunicationRequest():
    """Communication request class."""

    post_hook = None
    response = None

    class RequestType():
        """Request type class."""

        RequestJobs = 'request_jobs'
        UpdateJobs = 'update_jobs'
        RequestEvents = 'request_events'
        UpdateEvents = 'update_events'

    def __init__(self, attrs: dict = None):
        """
        Initialize variables.

        :param attrs: attributes dictionary (dict).
        """
        if not attrs:
            attrs = {}
        if not isinstance(attrs, dict):
            attrs = json.loads(attrs)

        if attrs['request_type'] == CommunicationRequest.RequestType.RequestJobs:
            def_attrs = {'num_jobs': 1, 'post_hook': None, 'response': None}
        if attrs['request_type'] == CommunicationRequest.RequestType.RequestEvents:
            def_attrs = {'num_event_ranges': 1, 'post_hook': None, 'response': None}
        if attrs['request_type'] == CommunicationRequest.RequestType.UpdateEvents:
            def_attrs = {'update_events': None, 'post_hook': None, 'response': None}
        if attrs['request_type'] == CommunicationRequest.RequestType.UpdateJobs:
            def_attrs = {'jobs': None, 'post_hook': None, 'response': None}

        for key, value in def_attrs.items():
            if key not in attrs:
                attrs[key] = value

        for key in attrs:
            setattr(self, key, attrs[key])

        self.abort = False

    def __str__(self):
        """
        Return string representation.

        :return: string representation (str).
        """
        json_str = {}
        for key, value in list(self.__dict__.items()):
            if value and isinstance(value, list):
                json_str[key] = []
                for list_item in value:
                    json_str[key].append(str(list_item))
            elif value:
                json_str[key] = str(value)
            else:
                json_str[key] = value

        return json.dumps(json_str)


class CommunicationManager(threading.Thread, PluginFactory):
    """Communication manager class."""

    def __init__(self, *args, **kwargs):
        """
        Initialize variables.

        :param args: args object (Any)
        :param kwargs: kwargs dictionary (dict).
        """
        super().__init__()
        PluginFactory.__init__(self, *args, **kwargs)
        self.name = "CommunicationManager"
        self.post_get_jobs = None
        self.post_get_event_ranges_hook = None
        self.queues = {'request_get_jobs': queue.Queue(),
                       'update_jobs': queue.Queue(),
                       'request_get_events': queue.Queue(),
                       'update_events': queue.Queue(),
                       'processing_get_jobs': queue.Queue(),
                       'processing_update_jobs': queue.Queue(),
                       'processing_get_events': queue.Queue(),
                       'processing_update_events': queue.Queue()}
        self.queue_limits = {'request_get_jobs': None,
                             'update_jobs': None,
                             'request_get_events': None,
                             'update_events': None,
                             'processing_get_jobs': 1,
                             'processing_update_jobs': 1,
                             'processing_get_events': 1,
                             'processing_update_events': 1}
        self.stop_event = threading.Event()
        self.args = args
        self.kwargs = kwargs

    def stop(self):
        """Set stop signal (main run process will clean queued requests to release waiting clients and then quit)."""
        if not self.is_stop():
            logger.info("stopping Communication Manager.")
            self.stop_event.set()

    def is_stop(self) -> bool:
        """
        Check whether the stop signal is set.

        :returns: True if the stop signal is set, otherwise False (bool)
        """
        return self.stop_event.is_set()

    def get_jobs(self, njobs: int = 1, post_hook: Any = None, args: Any = None) -> Any:
        """
        Get jobs.

        Function can be called by client to send a get_job request and get a response with jobs.

        :raises: Exception caught when getting jobs from server
        :return: jobs (from server) (Any).
        """
        if self.is_stop():
            return None

        req_attrs = {}
        if args:
            if not isinstance(args, dict):
                args = vars(args)
            for key, value in list(args.items()):
                req_attrs[key] = value

        other_req_attrs = {'request_type': CommunicationRequest.RequestType.RequestJobs,
                           'num_jobs': njobs,
                           'post_hook': post_hook}
        for key, value in list(other_req_attrs.items()):
            req_attrs[key] = value

        req = CommunicationRequest(req_attrs)
        self.queues['request_get_jobs'].put(req)

        if req.post_hook:
            return None

        while req.response is None:
            time.sleep(1)
        if req.response.exception:
            raise req.response.exception
        if req.response.status is False:
            return None

        return req.response.content

    def update_jobs(self, jobs: Any, post_hook: Any = None) -> Any:
        """
        Update jobs.

        Function can be called by client to update jobs' status to server.

        :param jobs: jobs to be updated (Any)
        :param post_hook: post hook function (Any)
        :raises: Exception caught when updating jobs
        :return: status of updating jobs (Any).
        """
        if self.is_stop():
            return None

        req_attrs = {'request_type': CommunicationRequest.RequestType.UpdateJobs,
                     'jobs': jobs,
                     'post_hook': post_hook}

        req = CommunicationRequest(req_attrs)
        self.queues['update_jobs'].put(req)

        if req.post_hook:
            return None

        while req.response is None:
            time.sleep(1)
        if req.response.exception:
            raise req.response.exception
        if req.response.status is False:
            return None

        return req.response.content

    def get_event_ranges(self, num_event_ranges: int = 1, post_hook: Any = None, job: Any = None) -> Any:
        """
        Get event ranges.

        Function can be called by client to send a get_event_ranges request and get a response with event ranges.

        :param num_event_ranges: number of event ranges to get (int)
        :param post_hook: post hook function (Any)
        :param job: job info (Any)
        :raise: Exception caught when getting event ranges
        :return: event ranges (from server) (Any).
        """
        if self.is_stop():
            return None

        if not job:
            resp_attrs = {'status': -1,
                          'content': None,
                          'exception': exception.CommunicationFailure(f"get events failed because job info missing "
                                                                      f"(job: {job})")}
            resp = CommunicationResponse(resp_attrs)
            if resp.exception is not None:
                raise resp.exception
            raise exception.CommunicationFailure(f"get events failed because job info missing (job: {job})")

        req_attrs = {'request_type': CommunicationRequest.RequestType.RequestEvents,
                     'num_event_ranges': num_event_ranges,
                     'post_hook': post_hook}
        req_attrs['jobid'] = job['PandaID']
        req_attrs['jobsetid'] = job['jobsetID']
        req_attrs['taskid'] = job['taskID']
        req_attrs['num_ranges'] = num_event_ranges

        req = CommunicationRequest(req_attrs)
        self.queues['request_get_events'].put(req)

        if req.post_hook:
            return None

        while req.response is None:
            time.sleep(1)
        if req.response.exception:
            raise req.response.exception
        if req.response.status is False:
            return None

        return req.response.content

    def update_events(self, update_events: Any, post_hook: Any = None) -> Any:
        """
        Update events.

        Function can be called by client to send a update_events request.

        :param update_events: update events (Any)
        :param post_hook: post hook function (Any)
        :raises: Exception caught when updating event ranges
        :return: status of updating event ranges
        """
        if self.is_stop():
            return None

        req_attrs = {'request_type': CommunicationRequest.RequestType.UpdateEvents,
                     'update_events': update_events,
                     'post_hook': post_hook}
        req = CommunicationRequest(req_attrs)
        self.queues['update_events'].put(req)

        if req.post_hook:
            return

        while req.response is None:
            time.sleep(1)
        if req.response.exception:
            raise req.response.exception
        if req.response.status is False:
            return None
        else:
            return req.response.content

    def get_plugin_confs(self) -> dict:
        """
        Get different plug-in for different communicator.

        :returns: dict with {'class': <plugin_class>} and other items (dict).
        """
        plugin = os.environ.get('COMMUNICATOR_PLUGIN', None)
        if not plugin:
            plugin_confs = {'class': 'pilot.eventservice.communicationmanager.plugins.pandacommunicator.PandaCommunicator'}
        elif plugin == 'act':
            plugin_confs = {'class': 'pilot.eventservice.communicationmanager.plugins.actcommunicator.ACTCommunicator'}
        elif plugin == 'harvestersf':
            plugin_confs = {'class': 'pilot.eventservice.communicationmanager.plugins.harvestersharefilecommunicator.HarvesterShareFileCommunicator'}
        else:
            plugin_confs = {'class': 'pilot.eventservice.communicationmanager.plugins.pandacommunicator.PandaCommunicator'}

        if self.args:
            for key, value in list(vars(self.args).items()):
                plugin_confs[key] = value

        return plugin_confs

    def can_process_request(self, processor: dict, process_type: str) -> bool:
        """
        Check whether it is ready to process request in a type.

        For request such as HarvesterShareFileCommunicator, it should check whether there are processing requests to
        avoid overwriting files.

        :param processor: processor dictionary (dict)
        :param process_type: process type (str)
        :return: True or False (bool).
        """
        if self.queues[process_type].empty():
            return False

        next_queue = processor[process_type]['next_queue']
        if next_queue is None or self.queue_limits[next_queue] is None:
            return True

        if self.queues[next_queue].qsize() < self.queue_limits[next_queue]:
            return True

        return False

    def get_processor(self) -> dict:
        """
        Get processor dictionary.

        :return: processor dictionary (dict).
        """
        confs = self.get_plugin_confs()
        logger.info(f"communication plugin confs: {confs}")
        communicator = self.get_plugin(confs)

        return {'request_get_jobs': {'pre_check': communicator.pre_check_get_jobs,
                                     'handler': communicator.request_get_jobs,
                                     'next_queue': 'processing_get_jobs',
                                     'process_req_post_hook': False},
                'request_get_events': {'pre_check': communicator.pre_check_get_events,
                                       'handler': communicator.request_get_events,
                                       'next_queue': 'processing_get_events',
                                       'process_req_post_hook': False},
                'update_jobs': {'pre_check': communicator.pre_check_update_jobs,
                                'handler': communicator.update_jobs,
                                'next_queue': None,
                                'process_req_post_hook': True},
                'update_events': {'pre_check': communicator.pre_check_update_events,
                                  'handler': communicator.update_events,
                                  'next_queue': None,
                                  'process_req_post_hook': True},
                'processing_get_jobs': {'pre_check': communicator.check_get_jobs_status,
                                        'handler': communicator.get_jobs,
                                        'next_queue': None,
                                        'process_req_post_hook': True},
                'processing_get_events': {'pre_check': communicator.check_get_events_status,
                                          'handler': communicator.get_events,
                                          'next_queue': None,
                                          'process_req_post_hook': True}
                }

    def run(self):
        """Handle communication requests."""
        processor = self.get_processor()

        while True:
            has_req = False
            for process_type in processor:
                if self.is_stop():
                    while not self.queues[process_type].empty():
                        req = self.queues[process_type].get()
                        logger.info(f"is going to stop, aborting request: {req}")
                        req.abort = True
                        resp_attrs = {'status': None,
                                      'content': None,
                                      'exception': exception.CommunicationFailure("Communication manager is stopping, abort this request")}
                        req.response = CommunicationResponse(resp_attrs)
                elif self.can_process_request(processor, process_type):
                    pre_check_resp = processor[process_type]['pre_check']()
                    if not pre_check_resp.status == 0:
                        continue

                    logger.info(f"processing {process_type}")

                    has_req = True
                    req = self.queues[process_type].get()

                    logger.info(f"processing {process_type} request: {req}")
                    res = processor[process_type]['handler'](req)
                    logger.info(f"processing {process_type} respone: {res}")

                    if res.status is False:
                        req.response = res
                    else:
                        next_queue = processor[process_type]['next_queue']
                        if next_queue:
                            self.queues[next_queue].put(req)
                        else:
                            req.response = res
                        process_req_post_hook = processor[process_type]['process_req_post_hook']
                        if process_req_post_hook and req.post_hook:
                            req.post_hook(res)
            if not has_req:
                if self.is_stop():
                    break
            time.sleep(1)

        logger.info("communication manager finished")
