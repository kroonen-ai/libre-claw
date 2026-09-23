window.__ModuleLoader__.load({
	id: "@deepseek-ai/dsh-client-ui-conversation",
	factory: (require) => {
		var module = { exports: {} };
		module.exports;
		let react = require("react");
		//#region lib/types/client/index.js
		const loadPanel = () => require.async("./client.panel.js");
		function Shell() {
			const [Panel, setPanel] = (0, react.useState)(null);
			return (0, react.createElement)("section", null, (0, react.createElement)("button", { onClick: async () => {
				const [first, second] = await Promise.all([loadPanel(), loadPanel()]);
				document.documentElement.dataset.sameChunk = String(first === second);
				setPanel(() => first.Panel);
			} }, "Load panel"), Panel ? (0, react.createElement)(Panel) : (0, react.createElement)("span", null, "Waiting for chunk"));
		}
		//#endregion
		module.exports = {
			inject: ["slots"],
			apply(ctx) {
				ctx.slots.register({
					name: "shell.overlay",
					id: "dynamic-fixture"
				}, Shell);
			}
		};
		return module.exports;
	}
});

//# sourceMappingURL=client.js.map
