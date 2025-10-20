import json,os
class ConfigLoader:
    def __init__(self, project_path=os.getcwd()):
        """初始化配置加载器，自动加载指定配置文件。

        :param config_file: 配置文件的名称，默认为 config.json
        """
        config_path = os.path.join(project_path, 'config.json')
        # plan_path = os.path.join(project_path, 'task_plan.json')
        self.load_config(config_path)
        # self.load_task_plan(plan_path)
    
    def load_config(self, path):
        """从指定路径加载配置文件并将其内容设置为类属性。
        :param path: 配置文件的完整路径
        """
        try:
            with open(path, 'r') as file:
                config = json.load(file)
                for key, value in config.items():
                    setattr(self, key, value)
        except FileNotFoundError:
            print(f"配置文件 {path} 未找到.")
        except json.JSONDecodeError:
            print(f"配置文件 {path} 格式错误.")

    def get_config(self):
        """返回当前加载的配置字典。"""
        return self.config
    
class PublicConfig:
    '''
    这个类用于声明一些公共的配置和关联管理，方便进行管理。
    '''
    TEMPLATE_MAP: dict = {
        # 语言键统一为小写
        "r": "r_template.jinja",
    }

    MODEL_MAPPING = {
        'OLS':'plm',
        'FE':'plm',
    }

    LIBRARY_REQUIREMENTS = {
        "r": {
            # 回归/建模
            "OLS": ["plm"],          # lm(), summary()
            "FE": ["plm"],             # plm(..., model="within")
            "RE": ["plm"],             # plm(..., model="random")

            # 预处理/数据结构
            "winsorize": ["DescTools"],    # DescTools::Winsorize
            "set_panel": ["plm"],          # pdata.frame()
            "clean_sample": [],            # base/Stats 完成

            # 模板辅助宏（不需要外部依赖）
            "set_category_controls": [],   # as.factor()
            "extract_coefficients": [],    # 基础数据框操作
            "install_required_packages": []# install.packages()/library()
        },
        "stata": {
            "OLS": [],
            "FE": [],
            "RE": [],
            "IV2SLS": [],   # Stata 自带 ivregress
            "PSM": [],      # Stata 自带 teffects psmatch
            "HECKMAN": [],  # Stata 自带 heckman
        },
    }