from utils.values import CFG_BASE_PATH, Extra
from utils.config_reader import YamlFileConfigReader, YamlFileSectionConfigReader

BINDING_STATE = CFG_BASE_PATH + 'binding_state.yaml'


class Config(Extra):
    def __init__(self, name: str, config_type: str, plugin_name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.name = name
        self.type = config_type
        self.plugin_name = plugin_name

    def __str__(self) -> str:
        return f'Config(name={self.name}, type={self.type}, plugin_name={self.plugin_name}, extra_params={self.extra_params()})'

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'type': self.type,
            'plugin_name': self.plugin_name,
            **self.extra_params()
        }


class ConfigInstance:
    def __init__(self, reader: YamlFileConfigReader, config: Config) -> None:
        self.reader = reader
        self.config = config

    def save(self):
        self.reader.save(self.config.to_dict())


class PluginBinding:

    def __init__(self) -> None:
        self.config_instances: dict[str, ConfigInstance] = {}

    def list_config(self, config_type: str = None) -> list[Config]:
        if not config_type:
            return [instance.config for instance in self.config_instances.values()]
        return [instance.config for instance in self.config_instances.values() if instance.config.type == config_type]

    def load_store(self):
        reader = YamlFileConfigReader(BINDING_STATE)
        state = reader.read()
        for key in state.keys():
            config = state[key]
            plugin_name = config.pop('plugin_name')
            name = config.pop('name')
            config_type = config.pop('type')
            instance = ConfigInstance(
                YamlFileSectionConfigReader(BINDING_STATE, name), Config(name, config_type, plugin_name, **config))
            self.config_instances[name] = instance

    def add(self, config_data: dict) -> None:
        name = config_data.pop('name')
        config_type = config_data.pop('type')
        plugin_name = config_data.pop('plugin_name')
        if not name or not config_type or not plugin_name:
            raise Exception('name and plugin_name are required')
        if name in self.config_instances:
            raise Exception('config already exists')

        config = Config(name, config_type, plugin_name, **config_data)
        self.__validate(config)

        instance = ConfigInstance(
            YamlFileSectionConfigReader(BINDING_STATE, name), config)
        instance.save()
        self.config_instances[name] = instance

    def remove(self, name: str) -> None:
        if name not in self.config_instances:
            raise Exception('config not found')
        reader = YamlFileConfigReader(BINDING_STATE)
        state = reader.read()
        del state[name]
        reader.save(state)
        del self.config_instances[name]

    def update(self, name: str, config_data: dict) -> None:
        if name not in self.config_instances:
            raise Exception('config not found')
        instance = self.config_instances[name]
        exists = instance.config.extra_params()
        extra_params = {}
        extra_params.update(exists)
        extra_params.update(config_data)

        # We only need extra para, ignore the necessary para, or function Config(xxx) will fail
        extra_params.pop('name')
        extra_params.pop('type')
        extra_params.pop('plugin_name')

        self.__validate(Config(name, instance.config.type,
                               instance.config.plugin_name, **extra_params))
        instance.config.put_extra_params(extra_params)
        instance.save()

    @staticmethod
    def __validate(config: Config):
        from core import plugin_manager
        plugin_definition = plugin_manager.kubespider_plugin_manager.get_plugin(
            config.plugin_name)
        if not plugin_definition:
            raise Exception('plugin not found')
        plugin_definition.validate(**config.extra_params())


kubespider_plugin_binding: PluginBinding = None
