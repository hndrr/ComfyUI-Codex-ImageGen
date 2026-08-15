import { app } from "../../scripts/app.js";

const NODE_CLASSES = new Set(["CodexExecImageGen", "CodexExecText"]);
const IMAGEGEN_NODE_CLASS = "CodexExecImageGen";
const MIN_NODE_WIDTH = 460;
const PROMPT_HEIGHT = 150;
const INSTRUCTION_HEIGHT = 100;
const MAX_IMAGE_INPUTS = 8;

function imageInputNumber(input) {
    if (input.name === "images") {
        return 1;
    }
    const match = input.name?.match(/^image_(\d+)$/);
    return match ? Number.parseInt(match[1], 10) : null;
}

function setupDynamicImageInputs(node) {
    const knownInputs = node._codexAllInputs ?? [];
    const knownNames = new Set(knownInputs.map((input) => input.name));
    for (const input of node.inputs ?? []) {
        if (!knownNames.has(input.name)) {
            knownInputs.push(input);
            knownNames.add(input.name);
        }
    }
    node._codexAllInputs = knownInputs;

    for (const input of node._codexAllInputs) {
        const number = imageInputNumber(input);
        if (number !== null) {
            input.label = `image_${number}`;
        }
    }
}

function syncConfiguredInputs(node) {
    setupDynamicImageInputs(node);
    for (const configured of node.inputs ?? []) {
        const configuredNumber =
            configured.name === "images"
                ? 1
                : Number.parseInt(configured.name?.match(/^images?_(\d+)$/)?.[1] ?? "", 10);
        const canonical = Number.isFinite(configuredNumber)
            ? node._codexAllInputs.find((input) => imageInputNumber(input) === configuredNumber)
            : node._codexAllInputs.find((input) => input.name === configured.name);
        if (canonical) {
            canonical.link = configured.link;
        }
    }
}

function applyDynamicImageInputs(node) {
    syncConfiguredInputs(node);
    const imageInputs = node._codexAllInputs
        .filter((input) => imageInputNumber(input) !== null)
        .sort((left, right) => imageInputNumber(left) - imageInputNumber(right));
    const otherInputs = node._codexAllInputs.filter((input) => imageInputNumber(input) === null);
    const highestConnected = imageInputs.reduce(
        (highest, input) => (input.link == null ? highest : Math.max(highest, imageInputNumber(input))),
        0,
    );
    const visibleCount = Math.min(MAX_IMAGE_INPUTS, Math.max(1, highestConnected + 1));
    node.inputs = [...imageInputs.slice(0, visibleCount), ...otherInputs];
    node.inputs.forEach((input, slotIndex) => {
        if (input.link == null) {
            return;
        }
        const link = app.graph?.links?.[input.link];
        if (link) {
            link.target_slot = slotIndex;
        }
    });
}

function styleMultilineWidget(node, name, height) {
    const widget = node.widgets?.find((candidate) => candidate.name === name);
    if (!widget || widget._codexMultilineStyled) {
        return;
    }
    widget._codexMultilineStyled = true;

    const originalComputeSize = widget.computeSize;
    widget.computeSize = function (width) {
        const size = originalComputeSize?.call(this, width) ?? [width, height];
        return [size[0], Math.max(size[1], height)];
    };

    const elements = [
        widget.inputEl,
        widget.textarea,
        widget.element?.querySelector?.("textarea"),
        widget.domElement?.querySelector?.("textarea"),
    ].filter(Boolean);
    for (const element of new Set(elements)) {
        element.style.minHeight = `${height}px`;
        element.style.height = `${height}px`;
        element.style.resize = "vertical";
    }
}

function applyAskCodexLayout(node) {
    applyDynamicImageInputs(node);
    styleMultilineWidget(node, "prompt", PROMPT_HEIGHT);
    styleMultilineWidget(node, "instruction", INSTRUCTION_HEIGHT);

    requestAnimationFrame(() => {
        const computed = node.computeSize();
        node.setSize([Math.max(MIN_NODE_WIDTH, computed[0]), computed[1]]);
        node.setDirtyCanvas(true, true);
        app.graph?.setDirtyCanvas(true, true);
        app.canvas?.setDirty(true, true);
    });
}

function scheduleAskCodexLayout(node) {
    applyAskCodexLayout(node);
    requestAnimationFrame(() => applyAskCodexLayout(node));
    setTimeout(() => applyAskCodexLayout(node), 100);
}

function migrateLegacyWorkflow(info, nodeName) {
    if (nodeName !== IMAGEGEN_NODE_CLASS) {
        return;
    }
    const values = info?.widgets_values;
    if (!Array.isArray(values) || values.length < 18 || !/^[1-8]$/.test(String(values[0]))) {
        if (
            Array.isArray(values) &&
            values.length === 11 &&
            /^[1-8]$/.test(String(values[1]))
        ) {
            info.widgets_values = [values[0], "", "auto", ...values.slice(1)];
        } else if (
            Array.isArray(values) &&
            values.length === 12 &&
            /^[1-8]$/.test(String(values[2]))
        ) {
            info.widgets_values = [values[0], values[1], "auto", ...values.slice(2)];
        }
        return;
    }

    const legacyModel = values[10];
    info.widgets_values = [
        values[1],
        "",
        "auto",
        values[0],
        values[9],
        legacyModel === "gpt-5.4" || legacyModel === "gpt-5.5" ? "gpt-5.6-sol" : legacyModel,
        values[11],
        ...values.slice(12, 18),
    ];
}

app.registerExtension({
    name: "codex.ask_codex_layout",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!NODE_CLASSES.has(nodeData.name)) {
            return;
        }

        const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalOnNodeCreated?.apply(this, arguments);
            scheduleAskCodexLayout(this);
            return result;
        };

        const originalOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            migrateLegacyWorkflow(info, nodeData.name);
            const result = originalOnConfigure?.apply(this, arguments);
            scheduleAskCodexLayout(this);
            return result;
        };

        const originalOnConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = originalOnConnectionsChange?.apply(this, arguments);
            setTimeout(() => applyAskCodexLayout(this), 0);
            return result;
        };
    },
});

console.log("[Codex] Ask Codex layout extension loaded");
