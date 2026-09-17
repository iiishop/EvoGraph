# UML 类图规范

用户提供的规范原文由 [uml-style.md](../backend/evograph/resources/uml-style.md) 统一维护。
该文件随 Python 包分发，并直接作为 save_uml 工具的绘图规范提供给 Agent。

类图使用 PlantUML；里程碑图仍采用左右连接点和弧线，不受类图折线规范影响。
本地需要 Java 与 PlantUML：`python tools/setup_uml.py` 安装固定版本的渲染器，
也可设置 `EVOGRAPH_PLANTUML_JAR` 指向已有 jar。使用 SANDBOX 和 Smetana，
无需 Graphviz，不将项目源码上传到公共绘图服务。

save_uml 编译成功后保存版本；类图固定为 class_model，其他图按 ID 独立维护，
milestone_ids 指定关联位置。源码图要求实际读取的源码引用，规划设计用 design。
编译校验不代表源码语义已被自动证明，仍需要依据图中引用核对。
