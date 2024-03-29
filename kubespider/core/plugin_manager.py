import os
import time
import subprocess
import _thread
import logging
import signal

from utils.definition import Definition
from utils.values import CFG_BASE_PATH, Resource
from utils.config_reader import YamlFileConfigReader, YamlFileSectionConfigReader
from utils import helper

PLUGIN_BASE_PATH = CFG_BASE_PATH + "plugins/"
PLUGIN_DEFINITION_PATH = PLUGIN_BASE_PATH + "definitions/"
PLUGIN_BINARY_PATH = PLUGIN_BASE_PATH + "binaries/"
PLUGIN_STATE_PATH = PLUGIN_BASE_PATH + "state.yaml"


class PluginInstance:
    def __init__(self, definition: Definition, state_reader: YamlFileConfigReader):
        self.definition = definition
        self.reader = state_reader
        self.state = state_reader.read()
        self.request = helper.get_request_controller()
        self.process = None

    def enable(self):
        port = self.state.get("port")
        if port:
            up = self.__helath()
            if up:
                return
        binary = PLUGIN_BINARY_PATH + self.definition.name
        if not os.path.exists(binary):
            self.__download_binary()

        plugin_port = helper.get_free_port()
        # start the plugin
        command = f"{binary} --name {self.definition.name} --port {plugin_port}"
        self.process = subprocess.Popen(command, shell=True)
        _thread.start_new_thread(self.__run_plugin, ())
        self.__update_state("port", plugin_port)
        # wait for the plugin to start
        for _ in range(10):
            if self.__helath():
                logging.info('Plugin started: %s', self.definition.name)
                return
            time.sleep(2)
        self.__update_state("port", None)
        raise Exception(f"Failed to start plugin: {self.definition.name}")

    def disable(self):
        self.__update_state("port", None)
        if not self.process:
            return
        logging.info('Stopping plugin: %s', self.definition.name)
        self.process.send_signal(signal.SIGTERM)

    def __helath(self) -> bool:
        try:
            self.call_api("_health")
            return True
        except Exception as e:
            logging.error('Plugin health check failed: %s', e)
            return False

    def call_api(self, api: str, **kwargs):
        port = self.state.get("port")
        if not port:
            raise Exception("Plugin not enabled")
        body = {
            "api": api,
            **kwargs
        }
        response = self.request.post(
            url=f"http://localhost:{port}", json=body, timeout=30)
        if response.status_code != 200:
            raise Exception(
                f"Failed to call plugin API: {api}")
        json = response.json()
        if json.get("code") != 200:
            raise Exception(
                f"Failed to call plugin API: {api}, {json.get('msg')}")

        return json.get("data")

    def __run_plugin(self):
        self.process.wait()

    def __download_binary(self):
        if not self.definition.binary:
            return
        if not os.path.exists(PLUGIN_BINARY_PATH):
            os.makedirs(PLUGIN_BINARY_PATH)
        # Download the binary
        response = self.request.get(self.definition.binary, timeout=30)
        if response.status_code != 200:
            raise Exception(
                f"Failed to download plugin binary: {self.definition.binary}")
        # When the binary exists, remove it
        binary_file = PLUGIN_BINARY_PATH + self.definition.name
        if os.path.exists(binary_file):
            os.remove(binary_file)
        # Save the binary
        with open(binary_file, "wb") as file:
            file.write(response.content)
        # Make the binary executable
        os.chmod(binary_file, 0o755)

    def __update_state(self, key: str, value: [str, int, None]):
        self.state[key] = value
        self.reader.save(self.state)


class PluginManager:
    def __init__(self):
        self.definitions: dict[str, Definition] = {}
        self.instances: dict[str, PluginInstance] = {}
        self.request = helper.get_request_controller()
        self.state = YamlFileConfigReader(PLUGIN_STATE_PATH).read()

    def list_plugin(self) -> list[Definition]:
        return list(self.definitions.values())

    def list_instance(self) -> list[PluginInstance]:
        return list(self.instances.values())

    def get_plugin(self, plugin_name: str) -> Definition:
        return self.definitions.get(plugin_name)

    def load_local(self) -> None:
        if not os.path.exists(PLUGIN_DEFINITION_PATH):
            os.makedirs(PLUGIN_DEFINITION_PATH)
            return
        # Load the plugin definitions
        for file in os.listdir(PLUGIN_DEFINITION_PATH):
            if file.endswith(".yaml"):
                logging.info('Loading plugin definition: %s', file)
                definition = Definition.init_from_yaml(PLUGIN_DEFINITION_PATH + file)
                self.definitions[definition.name] = definition
        # Auto enable when the state exists
        for definition in self.definitions.values():
            if self.state.get(definition.name) and self.state[definition.name].get("port", None):
                logging.info('Restore plugin instance: %s', definition.name)
                reader = YamlFileSectionConfigReader(
                    PLUGIN_STATE_PATH, definition.name)
                instance = PluginInstance(
                    definition, reader)
                instance.enable()
                self.instances[definition.name] = instance

    def register(self, yaml_file: str):
        # Download the plugin definition
        response = self.request.get(yaml_file, timeout=30)
        if response.status_code != 200:
            raise Exception(
                f"Failed to download plugin definition: {yaml_file}")

        # Read the plugin definition
        definition = Definition.init_from_yaml_bytes(response.content)
        # Check if the plugin definition already exists
        exists = self.definitions.get(definition.name)
        if exists:
            raise Exception(
                f"Plugin definition already exists: {definition.name}")
        # Save the plugin definition
        YamlFileConfigReader(PLUGIN_DEFINITION_PATH + definition.name + ".yaml").save(definition.serializer())
        self.definitions[definition.name] = definition

    def unregister(self, plugin_name: str):
        from core import plugin_binding
        configs = plugin_binding.kubespider_plugin_binding.list_config()
        for config in configs:
            if config.plugin_name == plugin_name:
                raise Exception(f'Plugin {plugin_name} is used by {config.name} currently, please delete it first...')
        definition = self.definitions.get(plugin_name)
        if not definition:
            raise Exception(f"Plugin not found: {plugin_name}")
        # Disable the plugin if it is enabled
        if self.instances.get(plugin_name):
            self.disable(plugin_name)
        # Remove the plugin definition and binary
        del self.definitions[plugin_name]
        try:
            os.remove(PLUGIN_DEFINITION_PATH + plugin_name + ".yaml")
            os.remove(PLUGIN_BINARY_PATH + plugin_name)
        except FileNotFoundError:
            pass

    def enable(self, plugin_name: str):
        definition = self.definitions.get(plugin_name)
        if not definition:
            raise Exception(f"Plugin not found: {plugin_name}")
        if self.instances.get(plugin_name):
            # Just ignore re-enable plugin operation
            return
        instance = PluginInstance(
            definition, YamlFileSectionConfigReader(PLUGIN_STATE_PATH, plugin_name))
        instance.enable()
        self.instances[plugin_name] = instance

    def disable(self, plugin_name: str):
        instance = self.instances.get(plugin_name)
        if not instance:
            raise Exception(f"Plugin not enabled: {plugin_name}")
        reader = YamlFileConfigReader(PLUGIN_STATE_PATH)
        self.state = reader.read()
        instance.disable()
        del self.instances[plugin_name]
        if self.state.get(plugin_name):
            del self.state[plugin_name]
            reader.save(self.state)

    def call(self, plugin_name: str, api_name: str, **kwargs) -> list[Resource]:
        instance = self.instances.get(plugin_name)
        if not instance:
            raise Exception(f"Plugin not enabled: {plugin_name}")
        return instance.call_api(api_name, **kwargs)


kubespider_plugin_manager: PluginManager = None
