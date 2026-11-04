"""Render a generated project's entry point on top of LangGraph.

The default converter keeps its current direct-call runner. This renderer is
used only when the caller explicitly enables LangGraph mode.
"""

from __future__ import annotations

import keyword
import re
from typing import Any

from n8n_transcriper.converters.loop import generate_loop_node
from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.reader import WorkflowError
from n8n_transcriper.versioning import compatible_node, manual_parameter_payload


_FOLDERS = {
    "n8n-nodes-base.code": "python_nodes",
    "n8n-nodes-base.convertToFile": "file_nodes",
    "n8n-nodes-base.compression": "file_nodes",
    "n8n-nodes-base.extractFromFile": "file_nodes",
    "n8n-nodes-base.httpRequest": "http_requests",
    "n8n-nodes-base.emailSend": "email_senders",
    "n8n-nodes-base.set": "operations",
    "n8n-nodes-base.noOp": "operations",
    "n8n-nodes-base.postgres": "database",
    "n8n-nodes-base.dataTable": "database",
    "n8n-nodes-base.merge": "operations",
    "n8n-nodes-base.wait": "operations",
    "n8n-nodes-base.if": "operations",
    "n8n-nodes-base.switch": "operations",
    "n8n-nodes-base.splitInBatches": "operations",
    "@n8n/n8n-nodes-langchain.agent": "ai_nodes",
}
_ITEMS_ONLY = {
    "n8n-nodes-base.code",
    "n8n-nodes-base.convertToFile",
    "n8n-nodes-base.compression",
    "n8n-nodes-base.extractFromFile",
    "n8n-nodes-base.noOp",
}


def _is_trigger(node: dict[str, Any]) -> bool:
    kind = str(node.get("type", ""))
    return kind.rsplit(".", 1)[-1].lower().endswith("trigger") or kind == "n8n-nodes-base.webhook"


def _python_alias(index: int) -> str:
    """Return a deterministic local name for an imported node function."""
    alias = f"_run_step_{index}"
    return alias if alias.isidentifier() and not keyword.iskeyword(alias) else f"_node_{index}"


def _folders(workflow_nodes: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Find generated module folders, including Gmail's specialized sender."""
    from n8n_transcriper.converters.smtp import is_gmail_send_node

    result = {}
    for name, node in workflow_nodes.items():
        kind = node["type"]
        folder = _FOLDERS.get(kind)
        if folder is None:
            continue
        if kind == "n8n-nodes-base.httpRequest" and is_gmail_send_node(node):
            folder = "email_senders"
        result[name] = folder
    return result


def _runtime_source() -> str:
    """Return small helpers embedded in generated main.py."""
    source = '''
def _flatten_ports(outputs):
    return [item for port in outputs for item in port]


def _link_items(source_items, output_items, node_name):
    for output_index, output_item in enumerate(output_items):
        paired = output_item.get("pairedItem")
        if isinstance(paired, dict):
            source_index = paired.get("item")
        elif isinstance(paired, list) and len(paired) == 1 and isinstance(paired[0], dict):
            source_index = paired[0].get("item")
        elif isinstance(paired, int) and not isinstance(paired, bool):
            source_index = paired
        elif len(source_items) == 1:
            source_index = 0
        elif len(source_items) == len(output_items):
            source_index = output_index
        else:
            continue
        if not isinstance(source_index, int) or isinstance(source_index, bool):
            continue
        if not 0 <= source_index < len(source_items):
            continue
        lineage = dict(source_items[source_index].get("_n8n_lineage", {}))
        lineage[node_name] = output_index
        output_item["_n8n_lineage"] = lineage
    return output_items


def _link_branch_items(source_items, output_ports, node_name):
    source_indexes = {id(item): index for index, item in enumerate(source_items)}
    flattened_index = 0
    for port_items in output_ports:
        for position, item in enumerate(list(port_items)):
            source_index = source_indexes.get(id(item))
            if source_index is None:
                flattened_index += 1
                continue
            linked = deepcopy(item)
            lineage = dict(source_items[source_index].get("_n8n_lineage", {}))
            lineage[node_name] = flattened_index
            linked["_n8n_lineage"] = lineage
            port_items[position] = linked
            flattened_index += 1
    return output_ports


def _clean_items(items):
    for item in items:
        item.pop("_n8n_lineage", None)
        item.pop("__transcriper_input", None)
        item.pop("__transcriper_index", None)
    return items


def _make_node(name, runner, parents, kind, output_count=1, execute_once=False,
               always_output_data=False, linked_items=False, inside_loop=False):
    async def invoke(state):
        outputs = dict(state.get("outputs", {}))
        results = dict(state.get("results", {}))
        execution = state["execution"]
        initial_items = state["input_data"]

        if kind == "n8n-nodes-base.merge":
            merge_inputs = [
                deepcopy(outputs.get(parent, [[]])[port])
                if len(outputs.get(parent, [])) > port else []
                for parent, port in parents
            ]
            items = [item for input_items in merge_inputs for item in input_items]
            has_input = any(merge_inputs)
        else:
            merge_inputs = []
            items = []
            if parents:
                for parent, port in parents:
                    parent_ports = outputs.get(parent, [[]])
                    if len(parent_ports) > port:
                        items.extend(deepcopy(parent_ports[port]))
            else:
                items = initial_items
            has_input = bool(items)

        if _is_trigger_name(kind):
            result = [items]
        elif kind == "n8n-nodes-base.merge":
            if has_input:
                result = [await runner(merge_inputs)]
            else:
                result = [[]]
        elif not has_input:
            result = [[] for _ in range(output_count)]
        else:
            if execute_once:
                items = items[:1]
            source_items = items
            if kind in _ITEMS_ONLY_KINDS:
                value = await runner(items)
            else:
                value = await runner(items, results, execution)
            if kind in _CONDITION_KINDS:
                result = value
                if linked_items:
                    result = _link_branch_items(source_items, result, name)
            else:
                value = value or []
                if always_output_data and not value:
                    value = [{"json": {}}]
                if linked_items:
                    value = _link_items(source_items, value, name)
                if kind == "n8n-nodes-base.dataTable":
                    for output_item in value:
                        output_item.pop("pairedItem", None)
                result = [value]

        outputs[name] = result
        if inside_loop and state.get("loop_runtime") is not None:
            results[name] = state["loop_runtime"].record_result(name, _flatten_ports(result))
        else:
            results[name] = _flatten_ports(result)
        return {"outputs": outputs, "results": results}

    return invoke


def _make_loop_node(name, loop_class, parents, loop_output, done_output,
                    output_count, body_graph, feedback_source, feedback_output,
                    batch_size, reset, execute_once, retry_on_fail, max_tries,
                    wait_between_tries, linked_items=False, manual_parameters=None):
    async def invoke(state):
        outputs = dict(state.get("outputs", {}))
        results = dict(state.get("results", {}))
        items = []
        for parent, port in parents:
            parent_ports = outputs.get(parent, [[]])
            if len(parent_ports) > port:
                items.extend(deepcopy(parent_ports[port]))

        loop_runtime = loop_class(
            batch_size=batch_size,
            reset=reset,
            execute_once=execute_once,
            retry_on_fail=retry_on_fail,
            max_tries=max_tries,
            wait_between_tries=wait_between_tries,
            n8n_manual_parameters=manual_parameters,
        )
        async for batch in loop_runtime.iterate(items):
            if linked_items:
                loop_runtime.link_batch(name, batch)
            loop_ports = [[] for _ in range(output_count)]
            loop_ports[loop_output] = batch
            body_outputs = dict(outputs)
            body_outputs[name] = loop_ports
            body_results = dict(results)
            body_results.update(loop_runtime.results)
            body_state = await body_graph.ainvoke({
                "input_data": batch,
                "execution": state["execution"],
                "outputs": body_outputs,
                "results": body_results,
                "loop_runtime": loop_runtime,
            })
            feedback_ports = body_state["outputs"].get(feedback_source, [[]])
            feedback_items = (
                feedback_ports[feedback_output]
                if len(feedback_ports) > feedback_output
                else []
            )
            await loop_runtime.collect(feedback_items)
            outputs.update(body_state["outputs"])
            results.update(body_state["results"])

        loop_ports = [[] for _ in range(output_count)]
        if done_output is not None:
            loop_ports[done_output] = deepcopy(loop_runtime.done_items)
        outputs[name] = loop_ports
        results.update(loop_runtime.results)
        results[name] = loop_runtime.done_items
        return {"outputs": outputs, "results": results}

    return invoke


def _is_trigger_name(kind):
    return kind.rsplit(".", 1)[-1].lower().endswith("trigger") or kind == "n8n-nodes-base.webhook"


_ITEMS_ONLY_KINDS = frozenset()
_CONDITION_KINDS = frozenset({"n8n-nodes-base.if", "n8n-nodes-base.switch"})
'''
    return source.replace(
        "_ITEMS_ONLY_KINDS = frozenset()",
        f"_ITEMS_ONLY_KINDS = frozenset({tuple(sorted(_ITEMS_ONLY))!r})",
    )


def render_langgraph_main(
    workflow: dict[str, Any],
    module_names: dict[str, str],
    loop_info: dict[str, Any] | None = None,
    agent_nodes: set[str] | None = None,
) -> str:
    """Generate ``main.py`` with every supported node represented in LangGraph.

    Acyclic workflows are represented as one LangGraph node per n8n node. The
    body of a Loop Over Items node becomes a nested graph, while the outer graph
    handles nodes before and after the loop.
    """
    nodes = {node["name"]: node for node in workflow["nodes"]}
    agent_nodes = agent_nodes or set()
    edges = connections(workflow)
    model_names = {
        edge.source
        for edge in edges
        if edge.channel == "ai_languageModel"
        and edge.target in nodes
        and nodes[edge.target].get("type") == "@n8n/n8n-nodes-langchain.agent"
    }
    main_nodes = set(nodes) - model_names
    all_main_edges = [edge for edge in edges if edge.channel == "main"]
    if loop_info is not None:
        return _render_loop_graph(workflow, nodes, module_names, loop_info, agent_nodes)
    if (
        any(node.get("type") == "n8n-nodes-base.splitInBatches" for node in nodes.values())
        and loop_info is None
    ):
        raise WorkflowError("Не удалось построить подграф LangGraph для Loop Over Items.")

    order = [name for name in execution_order(workflow) if name in main_nodes]
    main_edges = all_main_edges
    parents = {
        name: sorted(
            (edge for edge in main_edges if edge.target == name),
            key=lambda edge: edge.input,
        )
        for name in main_nodes
    }
    folders = _folders(nodes)
    for name in agent_nodes:
        if name in folders:
            folders[name] = "ai_nodes"
    linked_names = {
        name
        for node in nodes.values()
        for name in _linked_item_nodes(node.get("parameters", {}))
    }
    imports: list[str] = []
    aliases: dict[str, str] = {}
    loop_aliases: dict[str, str] = {}
    for index, name in enumerate(order):
        if _is_trigger(nodes[name]):
            continue
        if nodes[name].get("type") == "n8n-nodes-base.splitInBatches":
            alias = f"_LoopClass_{index}"
            loop_aliases[name] = alias
            imports.append(f"from operations.{module_names[name]} import Loop as {alias}")
            continue
        folder = folders.get(name)
        if folder is None:
            continue
        alias = _python_alias(index)
        aliases[name] = alias
        imports.append(
            f"from {folder}.{module_names[name]} import run_node as {alias}"
        )

    calls: list[str] = []
    for index, name in enumerate(order):
        node = nodes[name]
        if _is_trigger(node):
            runner = "None"
        else:
            runner = aliases.get(name)
            if runner is None:
                continue
        parent_data = [(edge.source, edge.output) for edge in parents[name]]
        kind = node["type"]
        output_count = 1
        if kind == "n8n-nodes-base.if":
            output_count = 2
        elif kind == "n8n-nodes-base.switch":
            rules = node.get("parameters", {}).get("rules", {}).get("values", [])
            fallback = node.get("parameters", {}).get("options", {}).get("fallbackOutput")
            output_count = len(rules) + (fallback == "extra")
        calls.extend(
            [
                f"    builder.add_node({('step_' + str(index))!r}, _make_node(",
                f"        {name!r}, {runner}, {parent_data!r}, {kind!r},",
                f"        output_count={output_count},",
                f"        execute_once={bool(node.get('executeOnce', False))!r},",
                f"        always_output_data={bool(node.get('alwaysOutputData', False))!r},",
                f"        linked_items={bool(linked_names)!r},",
                "    ))",
            ]
        )

    graph_node_names = [f"step_{index}" for index, name in enumerate(order) if _is_trigger(nodes[name]) or name in aliases]
    if not graph_node_names:
        raise ValueError("Workflow LangGraph не содержит исполняемых узлов.")
    calls.append(f"    builder.add_edge(START, {graph_node_names[0]!r})")
    calls.extend(
        f"    builder.add_edge({left!r}, {right!r})"
        for left, right in zip(graph_node_names, graph_node_names[1:], strict=False)
    )
    calls.append(f"    builder.add_edge({graph_node_names[-1]!r}, END)")

    outgoing = {name: 0 for name in main_nodes}
    for edge in main_edges:
        if edge.source in outgoing:
            outgoing[edge.source] += 1
    terminal_nodes = [
        name
        for name in order
        if outgoing.get(name, 0) == 0
        and nodes[name].get("type") not in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}
        and not _is_trigger(nodes[name])
    ]
    terminal_expr = " + ".join(
        f"state['outputs'].get({name!r}, [[]])[0]" for name in terminal_nodes
    ) or "[]"
    if linked_names:
        terminal_expr = f"_clean_items({terminal_expr})"

    source = (
        '"""Manual entry point for a workflow executed by LangGraph."""\n\n'
        "import asyncio\nimport logging\nimport sys\nimport uuid\n"
        "from copy import deepcopy\nfrom typing import Any, TypedDict\n\n"
        "from langgraph.graph import END, START, StateGraph\n\n"
        + "\n".join(imports)
        + "\n\nclass WorkflowState(TypedDict, total=False):\n"
        + "    input_data: list[dict[str, Any]]\n"
        + "    execution: dict[str, str]\n"
        + "    outputs: dict[str, list[list[dict[str, Any]]]]\n"
        + "    results: dict[str, list[dict[str, Any]]]\n\n"
        + _runtime_source()
        + "\n\n"
        + "def _build_graph():\n"
        + "    builder = StateGraph(WorkflowState)\n"
        + "\n".join(calls)
        + "\n    return builder.compile()\n\n\n"
        + "workflow_graph = _build_graph()\n\n\n"
        + "async def run_async(input_data=None):\n"
        + '    """Запустить граф и вернуть items его конечных узлов."""\n'
        + '    items = input_data if input_data is not None else [{"json": {}}]\n'
        + "    if isinstance(items, dict):\n        items = [items]\n"
        + "    state = await workflow_graph.ainvoke({\n"
        + "        'input_data': items,\n"
        + "        'execution': {'id': str(uuid.uuid4())},\n"
        + "        'outputs': {},\n"
        + "        'results': {},\n"
        + "    })\n"
        + f"    return {terminal_expr}\n\n\n"
        + "def run(input_data=None):\n"
        + '    """Синхронно запустить асинхронный LangGraph workflow."""\n'
        + '    if sys.platform == "win32":\n'
        + "        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:\n"
        + "            return runner.run(run_async(input_data))\n"
        + "    return asyncio.run(run_async(input_data))\n\n\n"
        + 'if __name__ == "__main__":\n'
        + "    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s', stream=sys.stdout)\n"
        + "    run()\n"
    )
    return source


def _render_loop_graph(
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    module_names: dict[str, str],
    loop_info: dict[str, Any],
    agent_nodes: set[str],
) -> str:
    """Собрать внешнюю последовательность и вложенный граф тела Loop."""
    full_order = loop_info["order"]
    body_names = loop_info["body"]
    body_order = [name for name in full_order if name in body_names]
    outer_order = [name for name in full_order if name not in body_names]
    if not body_order:
        raise WorkflowError(f"Узел {loop_info['name']}: тело цикла пустое.")

    main_edges = loop_info["dag_edges"]
    parents = {
        name: sorted(
            (edge for edge in main_edges if edge.target == name),
            key=lambda edge: edge.input,
        )
        for name in full_order
    }
    folders = _folders(nodes)
    for name in agent_nodes:
        if name in folders:
            folders[name] = "ai_nodes"
    linked_names = {
        referenced
        for node in nodes.values()
        for referenced in _linked_item_nodes(node.get("parameters", {}))
    }
    aliases: dict[str, str] = {}
    loop_aliases: dict[str, str] = {}
    imports: list[str] = []
    full_keys = {name: f"step_{index}" for index, name in enumerate(full_order)}
    body_keys = {name: f"body_step_{index}" for index, name in enumerate(body_order)}

    for index, name in enumerate(full_order):
        if _is_trigger(nodes[name]):
            continue
        if nodes[name].get("type") == "n8n-nodes-base.splitInBatches":
            alias = f"_LoopClass_{index}"
            loop_aliases[name] = alias
            imports.append(
                f"from operations.loop import Loop as {alias}"
            )
            continue
        folder = folders.get(name)
        if folder is None:
            raise WorkflowError(f"Узел {name}: не найден каталог сгенерированного модуля.")
        alias = _python_alias(index)
        aliases[name] = alias
        imports.append(
            f"from {folder}.{module_names[name]} import run_node as {alias}"
        )

    def node_call(name: str, key: str, indent: str, inside_loop: bool = False) -> list[str]:
        node = nodes[name]
        kind = node["type"]
        input_nodes = [(edge.source, edge.output) for edge in parents[name]]
        if kind == "n8n-nodes-base.splitInBatches":
            settings = loop_info["settings"]
            verified = generate_loop_node(compatible_node(node))
            manual_parameters = manual_parameter_payload(node) or None
            feedback = loop_info["feedback"]
            arguments = [
                f"{name!r}, {loop_aliases[name]}, {input_nodes!r},",
                f"{settings.loop_output}, {settings.done_output!r}, {settings.output_count},",
                "loop_body_graph,",
                f"{feedback.source!r}, {feedback.output},",
                f"{verified.batch_size}, {verified.reset!r}, {verified.execute_once!r},",
                f"{verified.retry_on_fail!r}, {verified.max_tries}, {verified.wait_between_tries!r},",
                f"linked_items={bool(linked_names)!r}, manual_parameters={manual_parameters!r}",
            ]
            return [
                f"{indent}builder.add_node({key!r}, _make_loop_node(",
                *(f"{indent}    {argument}" for argument in arguments),
                f"{indent}))",
            ]

        runner = "None" if _is_trigger(node) else aliases.get(name)
        if runner is None and not _is_trigger(node):
            raise WorkflowError(f"Узел {name}: не найден модуль сгенерированной функции.")
        output_count = 2 if kind == "n8n-nodes-base.if" else 1
        if kind == "n8n-nodes-base.switch":
            rules = node.get("parameters", {}).get("rules", {}).get("values", [])
            fallback = node.get("parameters", {}).get("options", {}).get("fallbackOutput")
            output_count = len(rules) + (fallback == "extra")
        return [
            f"{indent}builder.add_node({key!r}, _make_node(",
            f"{indent}    {name!r}, {runner}, {input_nodes!r}, {kind!r},",
            f"{indent}    output_count={output_count},",
            f"{indent}    execute_once={bool(node.get('executeOnce', False))!r},",
            f"{indent}    always_output_data={bool(node.get('alwaysOutputData', False))!r},",
            f"{indent}    linked_items={bool(linked_names)!r}, inside_loop={inside_loop!r},",
            f"{indent}))",
        ]

    def edges_for(keys: list[str], indent: str) -> list[str]:
        if not keys:
            return []
        result = [f"{indent}builder.add_edge(START, {keys[0]!r})"]
        result.extend(
            f"{indent}builder.add_edge({source!r}, {target!r})"
            for source, target in zip(keys, keys[1:], strict=False)
        )
        result.append(f"{indent}builder.add_edge({keys[-1]!r}, END)")
        return result

    body_lines = [
        "def _build_loop_body_graph():",
        "    builder = StateGraph(WorkflowState)",
    ]
    for name in body_order:
        body_lines.extend(node_call(name, body_keys[name], "    ", inside_loop=True))
    body_lines.extend(edges_for([body_keys[name] for name in body_order], "    "))
    body_lines.extend(["    return builder.compile()", "", "loop_body_graph = _build_loop_body_graph()"])

    graph_lines = [
        "def _build_graph():",
        "    builder = StateGraph(WorkflowState)",
    ]
    for name in outer_order:
        graph_lines.extend(node_call(name, full_keys[name], "    "))
    graph_lines.extend(edges_for([full_keys[name] for name in outer_order], "    "))
    graph_lines.extend(["    return builder.compile()", "", "workflow_graph = _build_graph()"])

    outgoing = {name: 0 for name in nodes}
    for edge in [*main_edges, loop_info["feedback"]]:
        outgoing[edge.source] = outgoing.get(edge.source, 0) + 1
    terminal_nodes = [
        name
        for name in full_order
        if outgoing.get(name, 0) == 0
        and nodes[name].get("type") not in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}
        and not _is_trigger(nodes[name])
    ]
    terminal = " + ".join(
        f"state['outputs'].get({name!r}, [[]])[0]" for name in terminal_nodes
    ) or f"state['results'].get({loop_info['name']!r}, [])"
    if linked_names:
        terminal = f"_clean_items({terminal})"

    source = (
        '"""Точка ручного запуска workflow через LangGraph."""\n\n'
        "import asyncio\nimport logging\nimport sys\nimport uuid\n"
        "from copy import deepcopy\nfrom typing import Any, TypedDict\n\n"
        "from langgraph.graph import END, START, StateGraph\n\n"
        + "\n".join(imports)
        + "\n\nclass WorkflowState(TypedDict, total=False):\n"
        + "    input_data: list[dict[str, Any]]\n"
        + "    execution: dict[str, str]\n"
        + "    outputs: dict[str, list[list[dict[str, Any]]]]\n"
        + "    results: dict[str, list[dict[str, Any]]]\n"
        + "    loop_runtime: Any\n\n"
        + _runtime_source()
        + "\n\n"
        + "\n".join(body_lines)
        + "\n\n"
        + "\n".join(graph_lines)
        + "\n\nasync def run_async(input_data=None):\n"
        + '    """Запустить граф и вернуть данные конечных узлов."""\n'
        + '    items = input_data if input_data is not None else [{"json": {}}]\n'
        + "    if isinstance(items, dict):\n        items = [items]\n"
        + "    state = await workflow_graph.ainvoke({\n"
        + "        'input_data': items,\n"
        + "        'execution': {'id': str(uuid.uuid4())},\n"
        + "        'outputs': {},\n"
        + "        'results': {},\n"
        + "    })\n"
        + f"    return {terminal}\n\n\n"
        + "def run(input_data=None):\n"
        + '    """Синхронная обёртка для запуска асинхронного графа."""\n'
        + '    if sys.platform == "win32":\n'
        + "        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:\n"
        + "            return runner.run(run_async(input_data))\n"
        + "    return asyncio.run(run_async(input_data))\n\n\n"
        + 'if __name__ == "__main__":\n'
        + "    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s', stream=sys.stdout)\n"
        + "    run()\n"
    )
    return source


def _linked_item_nodes(value: Any) -> set[str]:
    """Find references that need n8n paired-item lineage in the generated graph."""
    if isinstance(value, str):
        if not value.startswith("=") and "{{" not in value:
            return set()
        return {
            match.group(2)
            for match in re.finditer(r"\$\(\s*(['\"])(.*?)\1\s*\)\.item(?:\.|\[|$)", value)
        }
    if isinstance(value, dict):
        return set().union(*(_linked_item_nodes(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_linked_item_nodes(item) for item in value), set())
    return set()
