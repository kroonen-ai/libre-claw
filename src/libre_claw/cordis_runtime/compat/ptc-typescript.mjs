// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// String-only compilation: no project, filesystem host, or type checking.
import ts from 'typescript';

export function transpilePtc(source) {
  const result = ts.transpileModule(source, {
    fileName: 'ptc-program.ts',
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      isolatedModules: true,
      newLine: ts.NewLineKind.LineFeed,
    },
  });
  const error = result.diagnostics?.find(item => item.category === ts.DiagnosticCategory.Error);
  if (error) throw new SyntaxError(ts.flattenDiagnosticMessageText(error.messageText, '\n'));
  return result.outputText;
}
