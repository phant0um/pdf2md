// BatchProcessor.swift
// Bridge entre SwiftUI e binário Python pdf2md.
// Executa Process(), captura stdout JSON, publica progresso via @Published.
import Foundation
import Combine
import UserNotifications

struct ProgressoArquivo: Identifiable, Codable {
    let id: String       // path do arquivo origem
    let status: String   // "aguardando" | "processando" | "concluido" | "erro" | "cancelado" | "ignorado"
    let erro: String?
    let avisos: [String] // avisos de qualidade; [] = output limpo

    // Decoder customizado: aceita JSON sem "avisos" (versões anteriores do binário)
    private enum CodingKeys: String, CodingKey {
        case id, status, erro, avisos
    }

    init(id: String, status: String, erro: String?, avisos: [String] = []) {
        self.id = id
        self.status = status
        self.erro = erro
        self.avisos = avisos
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        status = try c.decode(String.self, forKey: .status)
        erro = try c.decodeIfPresent(String.self, forKey: .erro)
        avisos = (try? c.decode([String].self, forKey: .avisos)) ?? []
    }
}

@MainActor
class BatchProcessor: ObservableObject {
    @Published var progresso: [ProgressoArquivo] = []
    @Published var estaProcessando: Bool = false
    @Published var concluido: Bool = false
    @Published var duracaoTotal: TimeInterval?   // duração total da última conversão
    @Published var inicioConversao: Date?        // início da conversão em curso (feedback de tempo)
    @Published var erroFatal: String?            // falha de execução inteira (FIX 2): alerta no ContentView

    // Localiza binário Python embarcado no bundle
    private var caminhoBinario: URL? {
        Bundle.main.url(forResource: "pdf2md", withExtension: nil)
    }

    func iniciarConversao(
        arquivos: [URL],
        destino: URL?,
        vault: URL?,
        obsidian: Bool,
        llmFallback: Bool = false,
        llmURL: String? = nil,
        llmModelo: String? = nil,
        llmKey: String? = nil
    ) async {
        erroFatal = nil  // limpa alerta anterior (FIX 2)

        guard let binario = caminhoBinario else {
            // FIX 2: antes, binário ausente era só print no console e a UI
            // ficava sem feedback — agora vira alerta visível.
            erroFatal = "Binário pdf2md não encontrado no bundle"
            return
        }

        // Evita reentrância: ignora novo início enquanto uma conversão corre.
        // Sem isto, cancelar+reconverter cria duas execuções que se atropelam
        // no MainActor (zerando estaProcessando da nova).
        guard !estaProcessando else { return }

        // Confinamento ao home para DESTINO/VAULT (FIX 3): antes só a origem
        // era validada aqui — destino/vault fora do home eram rejeitados pelo
        // CLI com erro genérico sem explicação. Falha rápida com alerta claro.
        let home = FileManager.default.homeDirectoryForCurrentUser
        if let v = vault {
            let vSeguro = v.resolvingSymlinksInPath()
            guard Self.dentroDoHome(vSeguro.path, home: home) else {
                erroFatal = "Vault fora do diretório home"
                return
            }
        } else if let dest = destino {
            let destSeguro = dest.resolvingSymlinksInPath()
            guard Self.dentroDoHome(destSeguro.path, home: home) else {
                erroFatal = "Pasta de saída fora do diretório home"
                return
            }
        }

        estaProcessando = true
        concluido = false
        duracaoTotal = nil
        inicioConversao = Date()
        progresso.removeAll()  // cada execução exibe apenas seus próprios arquivos
        let inicioTotal = Date()

        // Sanitização: filtrar URLs fora do diretório home ANTES de processar
        let arquivosValidos: [URL] = arquivos.compactMap { url in
            let seguro = url.resolvingSymlinksInPath()
            // Confinamento com fronteira de componente (ver dentroDoHome):
            // home.path sem "/" final deixaria /Users/bob prefixar /Users/bobby.
            guard Self.dentroDoHome(seguro.path, home: home) else {
                let rejeitado = ProgressoArquivo(
                    id: url.path,
                    status: "erro",
                    erro: "Path fora do diretório home",
                )
                progresso.append(rejeitado)
                return nil
            }
            return seguro
        }

        // Inicializa progresso apenas para arquivos válidos
        let progressoInicial = arquivosValidos.map {
            ProgressoArquivo(id: $0.path, status: "aguardando", erro: nil)
        }
        progresso.append(contentsOf: progressoInicial)

        // Um ÚNICO processo para todos os arquivos válidos (sem spawn por
        // arquivo: cada spawn descompactava o runtime Python inteiro). A lista
        // vai por stdin, NUL-separada; o CLI emite uma linha JSON por evento.
        if !arquivosValidos.isEmpty {
            // Monta args: - [--vault PATH | DESTINO] [--obsidian] [--llm-fallback] --json
            var args: [String] = ["-"]

            if let v = vault {
                let vSeguro = v.resolvingSymlinksInPath()
                args.append("--vault")
                args.append(vSeguro.path)
            } else if let dest = destino {
                let destSeguro = dest.resolvingSymlinksInPath()
                args.append(destSeguro.path)
            }

            if obsidian {
                args.append("--obsidian")
            }

            if llmFallback {
                args.append("--llm-fallback")
            }

            // --json ao final (posição correta para Typer)
            args.append("--json")

            // Caminhos NUL-separados, com NUL final (o CLI descarta vazios)
            let entrada = Data((arquivosValidos.map(\.path).joined(separator: "\0") + "\0").utf8)

            // Config do LLM entra no environment do processo (D8) — NUNCA em
            // argv, que aparece em `ps aux` para qualquer processo do mesmo
            // usuário (CWE-522). O binário aplica precedência env > default.
            var envLLM: [String: String] = [:]
            if llmFallback {
                if let url = llmURL { envLLM["PDF2MD_LLM_URL"] = url }
                if let modelo = llmModelo { envLLM["PDF2MD_LLM_MODEL"] = modelo }
                if let key = llmKey { envLLM["PDF2MD_LLM_KEY"] = key }
            }

            let base = vault ?? destino

            // Cancelamento é responsabilidade do ProcessRunner: SIGTERM +
            // fechar pipes + SIGKILL em graça. A task cancelada retorna
            // AQUI imediatamente, sem depender do processo morrer.
            let resultado = await ProcessRunner.executar(
                binario: binario,
                args: args,
                env: envLLM.isEmpty ? nil : envLLM,
                entrada: entrada,
                // Watchdog escala com o lote: 30 min por arquivo, no mínimo 30 min.
                timeout: 30 * 60 * Double(max(1, arquivosValidos.count)),
                aoLinhaStdout: { linha in
                    // Chamado em background: só decodifica e pula para o MainActor.
                    guard let item = Self.decodificarEvento(linha) else { return }
                    Task { @MainActor in
                        self.aplicarEvento(item, base: base)
                    }
                }
            )

            // Reaplica todo o stdout, em ordem, antes de fechar o que sobrou.
            // O stdout completo é a fonte final: tasks atrasadas do callback
            // não têm ordem garantida. aplicarEvento é idempotente (final
            // repetido é igual; "processando" atrasado cai no guard), então
            // reaplicar não regride estado.
            for linha in String(decoding: resultado.stdout ?? Data(), as: UTF8.self).split(separator: "\n") {
                if let item = Self.decodificarEvento(String(linha)) {
                    aplicarEvento(item, base: base)
                }
            }

            // Finaliza os ids sem evento final (cancelado, falha do processo
            // ou CLI que não emitiu o resultado do arquivo).
            let pendentes = progresso
                .filter { $0.status == "aguardando" || $0.status == "processando" }
                .map(\.id)
            for id in pendentes {
                if Task.isCancelled {
                    atualizarProgresso(id: id, status: "cancelado", erro: nil)
                } else if resultado.exitCode != 0 {
                    // Exit != 0: o CLI rejeitou o lote (validação, Tesseract
                    // ausente, vault inválido…). Mensagem real no stderr (FIX 1).
                    atualizarProgresso(id: id, status: "erro", erro: Self.mensagemErro(resultado.stderr))
                } else {
                    atualizarProgresso(id: id, status: "erro", erro: "Sem resultado do conversor")
                }
            }
        }

        estaProcessando = false
        concluido = true  // conversão terminou (concluída ou cancelada) → habilita "Limpar"
        duracaoTotal = Date().timeIntervalSince(inicioTotal)
        inicioConversao = nil

        // Notifica só em término natural; cancelamento manual não dispara alerta.
        if !Task.isCancelled {
            emitirNotificacao()
        }
    }

    private func atualizarProgresso(id: String, status: String, erro: String?, avisos: [String] = []) {
        if let index = progresso.firstIndex(where: { $0.id == id }) {
            progresso[index] = ProgressoArquivo(id: id, status: status, erro: erro, avisos: avisos)
        }
    }

    /// Decodifica uma linha JSON do stdout do CLI. Linha inválida vira nil.
    nonisolated private static func decodificarEvento(_ linha: String) -> ProgressoArquivo? {
        guard let jsonData = linha.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(ProgressoArquivo.self, from: jsonData)
    }

    /// Aplica um evento de progresso emitido pelo CLI (uma linha JSON por evento).
    /// Estados finais nunca voltam a "processando": as tasks do MainActor
    /// não garantem ordem de chegada.
    private func aplicarEvento(_ item: ProgressoArquivo, base: URL?) {
        var evento = item
        // FIX 6: o JSON do CLI não distingue "destino já existe" de extensão
        // não suportada — infere pelo filesystem.
        if evento.status == "ignorado" && evento.avisos.isEmpty {
            evento = anexarMotivoIgnorado(evento, base: base)
        }
        if evento.status == "processando",
           let atual = progresso.first(where: { $0.id == evento.id })?.status,
           ["concluido", "erro", "ignorado", "cancelado"].contains(atual) {
            return
        }
        // Avisos de qualidade acompanham o resultado (ADR-0005):
        // o âmbar no statusIcon depende deles chegarem até aqui.
        atualizarProgresso(id: evento.id, status: evento.status, erro: evento.erro, avisos: evento.avisos)
    }

    /// Confinamento ao diretório home com fronteira de componente (FIX 3):
    /// home.path sem "/" final deixaria /Users/bob prefixar /Users/bobby.
    private static func dentroDoHome(_ path: String, home: URL) -> Bool {
        path == home.path || path.hasPrefix(home.path + "/")
    }

    /// Mensagem legível do stderr do CLI, truncada para o alerta/linha da
    /// lista. Fallback para o erro genérico quando não há stderr (FIX 1).
    static func mensagemErro(_ stderr: Data?) -> String {
        guard let stderr,
              let texto = String(data: stderr, encoding: .utf8)?
                  .trimmingCharacters(in: .whitespacesAndNewlines),
              !texto.isEmpty else {
            return "Falha ao executar processo"
        }
        return String(texto.prefix(300))
    }

    /// FIX 6: reconversão de arquivo existente chega como "ignorado" sem
    /// motivo (a GUI nunca passa --sobrescrever). A GUI chama o binário uma
    /// vez por lote (ids no stdin), não por arquivo. O destino esperado é
    /// `<base>/<stem>.md`; a checagem não reproduz o dedup de nomes do lote.
    /// Se o .md já existe, anexa aviso explicativo.
    private func anexarMotivoIgnorado(_ item: ProgressoArquivo, base: URL?) -> ProgressoArquivo {
        guard let base else { return item }
        let origem = URL(fileURLWithPath: item.id)
        let nomeMD = origem.deletingPathExtension().lastPathComponent + ".md"
        if FileManager.default.fileExists(atPath: base.appendingPathComponent(nomeMD).path) {
            return ProgressoArquivo(id: item.id, status: item.status, erro: nil, avisos: ["destino já existe"])
        }
        return item
    }

    private func emitirNotificacao() {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }

        let content = UNMutableNotificationContent()
        content.title = "pdf2md"
        let sucessos = progresso.filter { $0.status == "concluido" }.count
        let erros = progresso.filter { $0.status == "erro" }.count
        let cancelados = progresso.filter { $0.status == "cancelado" }.count
        var partes: [String] = []
        if sucessos > 0 { partes.append("\(sucessos) concluídos") }
        if erros > 0 { partes.append("\(erros) erros") }
        if cancelados > 0 { partes.append("\(cancelados) cancelados") }
        var corpo = partes.joined(separator: " · ")
        if let t = duracaoTotal {
            corpo += " em \(Self.formatarDuracao(t))"
        }
        content.body = corpo
        content.sound = .default

        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil
        )
        center.add(request)
    }

    /// Formata segundos legível: "1.2s" (<1min) ou "1m02s" (>=1min).
    /// Estático para reuso no ContentView (tempo total da conversão).
    static func formatarDuracao(_ seg: Double) -> String {
        if seg < 1 {
            return String(format: "%.0fms", seg * 1000)  // ms: conversão leva ms, não "0.0s"
        }
        if seg < 60 {
            return String(format: "%.1fs", seg)
        }
        let minutos = Int(seg) / 60
        let segundos = Int(seg) % 60
        return String(format: "%dm%02ds", minutos, segundos)
    }

    func limpar() {
        progresso.removeAll()
        estaProcessando = false
        concluido = false
        duracaoTotal = nil
        inicioConversao = nil
        erroFatal = nil
    }
}
