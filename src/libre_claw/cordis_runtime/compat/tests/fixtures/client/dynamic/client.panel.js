window.__ModuleLoader__.load({
	id: "@deepseek-ai/dsh-client-ui-conversation",
	chunk: "client.panel.js",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		let react = require("react");
		//#region lib/types/client/panel.js
		const counters = document.documentElement.dataset;
		counters.chunkMounts = String(Number(counters.chunkMounts ?? 0) + 1);
		function Panel() {
			return (0, react.createElement)("strong", null, "Compiled lazy panel");
		}
		//#endregion
		exports.Panel = Panel;
		return module.exports;
	}
});

//# sourceMappingURL=client.panel.js.map
