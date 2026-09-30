# UML 类图规范

当前有效的局部绘图规范由 [uml-style.md](../backend/evograph/resources/uml-style.md) 统一维护。
该文件随 Python 包分发，并直接作为 save_uml 工具的绘图规范提供给 Agent。

类图使用 PlantUML；里程碑图仍采用左右连接点和弧线，不受类图折线规范影响。
本地需要 Java 与 PlantUML：`python tools/setup_uml.py` 安装固定版本的渲染器，
也可设置 `EVOGRAPH_PLANTUML_JAR` 指向已有 jar。使用 SANDBOX 和 Smetana，
无需 Graphviz，不将项目源码上传到公共绘图服务。

save_uml 编译成功后保存版本。类图必须绑定选中的架构组件和对应版本，仅描述局部结构，不维护全项目主类图；旧 class_model 历史数据保留。其他图按 ID 独立维护。
milestone_ids 指定关联位置。源码图要求实际读取的源码引用，规划设计用 design。
编译校验不代表源码语义已被自动证明，仍需要依据图中引用核对。

原先完整绘图参考保存在 [历史规范](uml-style-legacy.md)，仅归档，不传给 Agent；其中全项目总图要求已被局部范围工作流取代。
