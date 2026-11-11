// ProcessRunner.swift
// Execução de Process() com drenagem concorrente de stdout/stderr e
// cancelamento ROBUSTO embutido (único dono do ciclo de vida do processo).
//
// Padrão único para os dois pontos que spawnam o binário pdf2md
// (BatchProcessor — conversão; LLMProbe/SettingsView — diagnóstico).
// Ler os pipes EM SEQUÊNCIA causa deadlock: o filho só fecha stderr ao
// sair, e só sai após escrever todo o stdout — se o stdout exceder o
// buffer do pipe (~64KB), o filho bloqueia em write() para sempre.
//
// Cancelamento: o handler de cancelamento de `withTaskCancellationHandler`
// (1) manda SIGTERM ao processo, (2) FECHA os pipes e (3) agenda SIGKILL
// como fallback. Fechar os pipes destrava `readToEnd()` imediatamente, mesmo
// que o processo (ou órfãos) ignore sinais — sem isto, o app ficava preso
// para sempre em "processando" quando o SIGTERM ficava pendente (ex.: OCR
// de página grande preso em chamada C) e a única saída era matar o app.
import Foundation
import Darwin

/// Resultado completo de uma execução do binário (FIX 1): stdout, stderr e
/// código de saída. Antes, o stderr era drenado e descartado e o exit code
/// nunca verificado — qualquer falha virava "Falha ao executar processo".
struct ResultadoProcesso: Sendable {
    let stdout: Data?
    let stderr: Data?
    let exitCode: Int32

    /// Processo nem chegou a iniciar (run() lançou ou task já cancelada).
    static let naoIniciado = ResultadoProcesso(stdout: nil, stderr: nil, exitCode: -1)
}

enum ProcessRunner {
    /// Executa o binário com env, drenando stdout e stderr concorrentemente.
    ///
    /// O cancelamento da task que chama este método encerra o processo e
    /// destrava o retorno imediatamente (ver doc do enum).
    ///
    /// - Parameters:
    ///   - binario: URL do executável.
    ///   - args: Argumentos (a key do LLM NUNCA entra aqui — só via `env`).
    ///   - env: Environment a injetar (merges com o do processo pai).
    ///   - timeout: Watchdog — conversão presa além deste tempo é encerrada.
    ///   - entrada: Dados escritos no stdin do processo (ex.: lista NUL-separada
    ///     de arquivos). `nil` mantém o comportamento antigo (stdin herdado).
    ///     A escrita roda em background, para nunca travar com pipe cheio.
    ///   - aoLinhaStdout: Chamado a cada linha do stdout ASSIM QUE ela chega
    ///     (em background, fora do MainActor). A lista `stdout` do resultado
    ///     continua completa ao final.
    /// - Returns: stdout, stderr e código de saída (ver `ResultadoProcesso`).
    static func executar(
        binario: URL,
        args: [String],
        env: [String: String]? = nil,
        entrada: Data? = nil,
        timeout: TimeInterval = 30 * 60,
        aoLinhaStdout: (@Sendable (String) -> Void)? = nil
    ) async -> ResultadoProcesso {
        // Cancelado antes mesmo de spawnar? Não inicia nada.
        if Task.isCancelled { return .naoIniciado }

        let processo = Process()
        processo.executableURL = binario
        processo.arguments = args
        if let env {
            var ambiente = ProcessInfo.processInfo.environment
            for (chave, valor) in env {
                ambiente[chave] = valor
            }
            processo.environment = ambiente
        }

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        processo.standardOutput = stdoutPipe
        processo.standardError = stderrPipe

        let stdinPipe = Pipe()
        if entrada != nil {
            processo.standardInput = stdinPipe
            // Sem SIGPIPE: se o filho sair sem ler tudo, o write falha com EPIPE
            // (tratado pelo `try?`) em vez de derrubar o app.
            _ = fcntl(stdinPipe.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
        }

        do {
            try processo.run()
        } catch {
            return .naoIniciado
        }

        if let entrada {
            // Background: se o filho não ler tudo, o write bloquearia a task.
            DispatchQueue.global().async {
                try? stdinPipe.fileHandleForWriting.write(contentsOf: entrada)
                try? stdinPipe.fileHandleForWriting.close()
            }
        }

        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                var resumiu = false
                let resumir: (ResultadoProcesso) -> Void = { resultado in
                    if !resumiu {
                        resumiu = true
                        continuation.resume(returning: resultado)
                    }
                }

                // Watchdog: nenhuma conversão legítima passa do timeout;
                // encerra (e destrava) o mesmo caminho do cancelamento.
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
                    if processo.isRunning {
                        encerrarProcesso(processo, pipes: [stdoutPipe, stderrPipe])
                    }
                }

                Task.detached {
                    // Leitura concorrente dos dois pipes — sem isto, deadlock
                    // quando o stdout enche o pipe (~64KB) antes do término.
                    async let stderr: Data? = try? stderrPipe.fileHandleForReading.readToEnd()
                    var linhas: [String] = []
                    do {
                        // Linha a linha: eventos do CLI chegam ao caller na hora.
                        // Pipe fechado no cancelamento/watchdog encerra ou lança
                        // a sequência; o que já chegou fica em `linhas`.
                        for try await linha in stdoutPipe.fileHandleForReading.bytes.lines {
                            linhas.append(linha)
                            aoLinhaStdout?(linha)
                        }
                    } catch {
                        // Leitura interrompida (pipe fechado): sem stdout adicional.
                    }
                    let err = await stderr
                    processo.waitUntilExit()
                    let dados = linhas.joined(separator: "\n").data(using: .utf8) ?? Data()
                    resumir(ResultadoProcesso(
                        stdout: dados.isEmpty ? nil : dados,
                        stderr: err,
                        exitCode: processo.terminationStatus
                    ))
                }
            }
        } onCancel: {
            encerrarProcesso(processo, pipes: [stdoutPipe, stderrPipe])
        }
    }

    /// Encerra o processo e garante que a espera destrave, em 3 passos:
    /// 1. SIGTERM cooperativo (python encerra a página/arquivo em curso).
    /// 2. Fechar os pipes → `readToEnd()` retorna NA HORA, mesmo com
    ///    órfãos segurando o fd (o app nunca mais fica preso).
    /// 3. SIGKILL após 2s de graça, se o SIGTERM foi ignorado.
    private static func encerrarProcesso(_ processo: Process, pipes: [Pipe]) {
        if processo.isRunning {
            processo.terminate()
        }
        for pipe in pipes {
            try? pipe.fileHandleForReading.close()
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
            if processo.isRunning {
                kill(processo.processIdentifier, SIGKILL)
            }
        }
    }
}
