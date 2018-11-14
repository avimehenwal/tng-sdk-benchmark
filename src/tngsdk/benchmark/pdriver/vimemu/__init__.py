#  Copyright (c) 2018 SONATA-NFV, 5GTANGO, Paderborn University
# ALL RIGHTS RESERVED.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Neither the name of the SONATA-NFV, 5GTANGO, Paderborn University
# nor the names of its contributors may be used to endorse or promote
# products derived from this software without specific prior written
# permission.
#
# This work has been performed in the framework of the SONATA project,
# funded by the European Commission under Grant number 671517 through
# the Horizon 2020 and 5G-PPP programmes. The authors would like to
# acknowledge the contributions of their colleagues of the SONATA
# partner consortium (www.sonata-nfv.eu).
#
# This work has also been performed in the framework of the 5GTANGO project,
# funded by the European Commission under Grant number 761493 through
# the Horizon 2020 and 5G-PPP programmes. The authors would like to
# acknowledge the contributions of their colleagues of the SONATA
# partner consortium (www.5gtango.eu).
import logging
import os
import time
from tngsdk.benchmark.pdriver.vimemu.emuc import LLCMClient
from tngsdk.benchmark.pdriver.vimemu.emuc import EmuSrvClient
from tngsdk.benchmark.pdriver.vimemu.dockerc import EmuDockerClient


LOG = logging.getLogger(os.path.basename(__file__))


# global configurations
WAIT_SHUTDOWN_TIME = 4  # FIXME give experiment some cooldown time
WAIT_PADDING_TIME = 3  # FIXME extra time to wait (to have some buffer)
PATH_SHARE = "/tngbench_share"
PATH_CMD_START_LOG = "cmd_start.log"
PATH_CMD_STOP_LOG = "cmd_stop.log"


class VimEmuDriver(object):
    # FIXME Public API of this class is the
    # prototype for the generic driver API.

    def __init__(self, args, config):
        self.args = args
        self.config = config
        self.emusrv_url = ("http://{}:{}"
                           .format(config.get("host"),
                                   config.get("emusrv_port")))
        self.llcm_url = ("http://{}:{}"
                         .format(config.get("host"),
                                 config.get("llcm_port")))
        self.docker_url = ("tcp://{}:{}"
                           .format(config.get("host"),
                                   config.get("docker_port")))
        # initialize sub-driver
        self.emusrvc = EmuSrvClient(self.emusrv_url)
        self.llcmc = LLCMClient(self.llcm_url)
        self.emudocker = EmuDockerClient(self.docker_url)
        LOG.info("Initialized VimEmuDriver with {}"
                 .format(self.config))

    def setup_platform(self):
        # check connectivity to target
        self.emusrvc.check_platform_ready()

    def setup_experiment(self, ec):
        # start emulator
        self.emusrvc.start_emulation()
        # wait for emulator ready
        self.emusrvc.wait_emulation_ready(self.llcmc)
        # upload package
        ns_uuid = self.llcmc.upload_package(ec.package_path)
        # instantiate service
        nsi_uuid = self.llcmc.instantiate_service(ns_uuid)
        LOG.info("Instantiated servie: {}".format(nsi_uuid))
        # wait for service beeing ready
        # setup monitoring?
        pass

    def execute_experiment(self, ec):
        # FIXME currently the keys for selecting the MPs are fixed
        # FIXME not nice, lots of hard coding, needs more flexability
        MP_IN_KEY = "mp::mp.input::"
        MP_OUT_KEY = "mp::mp.output::"
        # collect names of MPs
        mp_in_name = ec.parameter.get("{}name".format(MP_IN_KEY))
        mp_out_name = ec.parameter.get("{}name".format(MP_OUT_KEY))
        # collect commands for MPs
        mp_in_cmd_start = ec.parameter.get("{}cmd_start".format(MP_IN_KEY))
        mp_in_cmd_stop = ec.parameter.get("{}cmd_stop".format(MP_IN_KEY))
        mp_out_cmd_start = ec.parameter.get("{}cmd_start".format(MP_OUT_KEY))
        mp_out_cmd_stop = ec.parameter.get("{}cmd_stop".format(MP_OUT_KEY))
        # trigger MP commands: we always execute the commands in the following
        # order:
        # 1. mp_out_cmd_start
        # 2. mp_in_cmd_start
        # - run the experiment -
        # 3. mp_in_cmd_stop
        # 4. mp_out_cmd_stop
        # FIXME make this user-configurable and more flexible
        self.emudocker.execute(mp_out_name, mp_out_cmd_start,
                               os.path.join(PATH_SHARE, PATH_CMD_START_LOG))
        self.emudocker.execute(mp_in_name, mp_in_cmd_start,
                               os.path.join(PATH_SHARE, PATH_CMD_START_LOG))
        self._wait_experiment(ec)
        self.emudocker.execute(mp_in_name, mp_in_cmd_stop,
                               os.path.join(PATH_SHARE, PATH_CMD_STOP_LOG))
        self.emudocker.execute(mp_out_name, mp_out_cmd_stop,
                               os.path.join(PATH_SHARE, PATH_CMD_STOP_LOG))
        self._wait_time(WAIT_SHUTDOWN_TIME,
                        "Finalizing experiment '{}'".format(ec))
        # collect results
        self._collect_experiment_results(ec)
        LOG.info("Finalized '{}'".format(ec))
        # TODO remove when deployment works
        print("Wait for user input...")
        input()

    def teardown_experiment(self, ec):
        self.emusrvc.stop_emulation()

    def teardown_platform(self):
        pass

    def _collect_experiment_results(self, ec):
        LOG.info("Collecting experiment results ...")
        # generate result paths
        dst_path = os.path.join(self.args.result_dir, ec.name)
        # for each container collect files from containers
        for c in self.emudocker.list_emu_containers():
            c_dst_path = os.path.join(dst_path, c.name)
            self.emudocker.copy_folder(c.name, PATH_SHARE, c_dst_path)
        # TODO colelct continous monitoring data (per container, global?)

    def _wait_experiment(self, ec, text="Running experiment"):
        time_limit = int(ec.parameter.get("header::all::time_limit", 0))
        if time_limit < 1:
            return  # we don't need to wait
        time_limit += WAIT_PADDING_TIME
        self._wait_time(time_limit, "{} '{}'".format(text, ec))

    def _wait_time(self, time_limit, text="Wait"):
        WAIT_NUMBER_OF_OUTPUTS = 10  # FIXME make configurable
        if time_limit < 1:
            return  # we don't need to wait
        time_slot = int(time_limit / WAIT_NUMBER_OF_OUTPUTS)
        # wait and print status
        for i in range(0, WAIT_NUMBER_OF_OUTPUTS):
            time.sleep(time_slot)
            LOG.debug("{}\t... {}%"
                      .format(text, (100 / WAIT_NUMBER_OF_OUTPUTS) * (i + 1)))
