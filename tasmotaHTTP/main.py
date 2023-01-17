"""
An Tasmota HTTP plugin
"""

import six
import time
import requests
import json
from plugins.base import om_expose, OMPluginBase, PluginConfigChecker, background_task
import logging

logger = logging.getLogger(__name__)


class TasmotaHTTP(OMPluginBase):
    """
    An Tasmota HTTP plugin
    """

    name = 'tasmotaHTTP'
    version = '1.0.4'
    interfaces = [('config', '1.0')]

    config_description = [{'name': 'refresh_interval',
                           'type': 'int',
                           'description': 'Refresh interval (in seconds) to fetch values from outputs and push to tasmota devices'},
                           {'name': 'max_retries',
                           'type': 'int',
                           'description': 'Maximum amount of retries to fetch values from outputs or push to tasmota devices'},
                           {'name': 'clear_interval',
                           'type': 'int',
                           'description': 'Interval (in minutes) to clear maximum retries per output'},
                          {'name': 'tasmota_mapping',
                           'type': 'section',
                           'description': 'Mapping betweet OpenMotics Virtual Sensors and Tasmota devices. See README.',
                           'repeat': True,
                           'min': 0,
                           'content': [{'name': 'label',
                                        'type': 'str',
                                        'description': 'Name to identify pair (ip_address, output_id)'},
                                       {'name': 'ip_address',
                                        'type': 'str',
                                        'description': 'Device IP Address.'},
                                       {'name': 'username',
                                        'type': 'str',
                                        'description': 'Device username, fill only if authentication is enabled.'},
                                       {'name': 'password',
                                        'type': 'password',
                                        'description': 'Device password, fill only if authentication is enabled.'},
                                       {'name': 'output_id',
                                        'type': 'int',
                                        'description':'OpenMotics output id to sync with Tasmota'}]}]

    default_config = {'refresh_interval': 5, 'max_retries': 20, 'clear_interval': 30}
    tasmota_http_endpoint = 'http://{ip_address}/cm?user={user}&password={password}&cmnd=Power%20{action}'

    def __init__(self, webinterface, connector):
        super(TasmotaHTTP, self).__init__(webinterface=webinterface,
                                            connector=connector)
        logger.info('Starting Tasmota HTTP plugin...')

        self._config = self.read_config(TasmotaHTTP.default_config)
        self._config_checker = PluginConfigChecker(TasmotaHTTP.config_description)

        self._read_config()

        self._previous_output_state = {}

        logger.info("Started Tasmota HTTP plugin")

    def _read_config(self):
        self._refresh_interval = self._config.get('refresh_interval', self.default_config['refresh_interval'])
        self._max_retries = self._config.get('max_retries', self.default_config['max_retries'])
        self._clear_interval = self._config.get('clear_interval', self.default_config['clear_interval'])
        self._max_retries_arr = {}
        self._clear_interval_arr = {}

        tasmota_mapping = self._config.get('tasmota_mapping', [])
        self._tasmota_mapping = tasmota_mapping
        self._headers = {'X-Requested-With': 'OpenMotics plugin: Tasmota HTTP'}
        self._enabled = bool(self._tasmota_mapping)

        for device in self._tasmota_mapping:
            if not isinstance(device['output_id'], int):
                continue
            device_output_id = device['output_id']
            self._max_retries_arr[device_output_id] = 0
            self._clear_interval_arr[device_output_id] = 0

        logger.info('Tasmota HTTP is {0}'.format('enabled' if self._enabled else 'disabled'))

    @background_task
    def run(self):
        previous_values = {}
        device_output_id = -1
        while True:
            if self._enabled:
                result = json.loads(self.webinterface.get_output_status())
                if 'status' in result:
                    for device in self._tasmota_mapping:
                        if not isinstance(device['output_id'], int):
                            continue
                        device_output_id = device['output_id']

                        if (self._clear_interval_arr[device_output_id] != 0) and ((self._clear_interval_arr[device_output_id] + (self._clear_interval * 60)) < int(time.time())):
                            logger.info("Resetting max retries for output {0}".format(device_output_id))
                            self._max_retries_arr[device_output_id] = 0
                            self._clear_interval_arr[device_output_id] = 0

                        if self._max_retries_arr[device_output_id] > self._max_retries:
                            continue

                        for output in result['status']:
                            output_id = output['id']
                            if output_id != device_output_id:
                                continue
                            if device['label'] in previous_values and previous_values[device['label']] == output['status']:
                                continue
                            try:
                                previous_values[device['label']] = self.update_tasmota(device, output)
                                self._max_retries_arr[device_output_id] = 0
                                self._clear_interval_arr[device_output_id] = 0
                                logger.info('Tasmota device {0} is {1}'.format(device['label'], 'on' if output['status'] == 1 else 'off'))
                            except Exception as ex:
                                self._max_retries_arr[device_output_id] += 1
                                logger.exception('Error: {0}'.format(ex))

                                if self._max_retries_arr[device_output_id] > self._max_retries:
                                    self._clear_interval_arr[device_output_id] = int(time.time()) # time in seconds
                                    logger.info('{0} reached max retries. Going idle for {1} minutes.'.format(device['label'], self._clear_interval))

                # Wait a given amount of seconds
                time.sleep(self._refresh_interval)
            else:
                time.sleep(5)

    @om_expose
    def get_config_description(self):
        return json.dumps(TasmotaHTTP.config_description)

    @om_expose
    def get_config(self):
        return json.dumps(self._config)

    @om_expose
    def set_config(self, config):
        config = json.loads(config)
        for key in config:
            if isinstance(config[key], six.string_types):
                config[key] = str(config[key])
        self._config_checker.check_config(config)
        self._config = config
        self._read_config()
        self.write_config(config)
        return json.dumps({'success': True})

    def update_tasmota(self, device, output):
        response = requests.get(url=self.tasmota_http_endpoint.format(ip_address=device['ip_address'],
                                                                           user=device['username'],
                                                                           password=device['password'],
                                                                           action=output['status']),
                                headers=self._headers)
        if response.status_code == 200:
            if response.json()['POWER'] == 'ON':
                return 1
            return 0

        raise Exception('Failed to update Tasmota device {0}: {1}'.format(device['ip_address'], response.status_code))
