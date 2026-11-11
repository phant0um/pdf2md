// SettingsView.swift
// Janela de Preferências (scene `Settings`) — provedor, modelo e API key do LLM.
//
// A lista de modelos e o status vêm do binário (pdf2md llm modelos/testar
// --json), nunca reimplementados em Swift. Swift só faz parse do JSON —
// mesmo padrão do protocolo usado no BatchProcessor. A key é lida do
// Keychain (D7) e vai para o binário via environment (D8).
import AppKit
import SwiftUI

struct ModeloLLM: Identifiable, Codable {
    let id: String
    let visao: Bool?
}

private struct RespostaModelos: Codable {
    let ok: Bool
    let modelos: [ModeloLLM]
    let erro: String?
}

private struct RespostaTeste: Codable {
    let ok: Bool
    let latencia_ms: Int?
    let erro: String?
}

/// Executa o binário embarcado `pdf2md` com o environment do LLM.
/// A key viaja no environment — nunca em argv (CWE-522 / `ps aux`).
/// Drenagem concorrente de pipes via ProcessRunner (sem deadlock).
enum LLMProbe {
    static func executar(_ args: [String], url: String?, key: String?) async -> Data? {
        guard let binario = Bundle.main.url(forResource: "pdf2md", withExtension: nil) else {
            return nil  // modo dev sem binário embarcado → UI usa lista estática
        }
        var env: [String: String] = [:]
        if let url { env["PDF2MD_LLM_URL"] = url }
        if let key { env["PDF2MD_LLM_KEY"] = key }
        let resultado = await ProcessRunner.executar(binario: binario, args: args, env: env)
        return resultado.stdout
    }
}

struct SettingsView: View {
    @AppStorage(LLMDefaultsKeys.provider) private var providerRaw = LLMProvider.ollama.rawValue
    @AppStorage(LLMDefaultsKeys.modelo) private var modelo = ""
    @AppStorage(LLMDefaultsKeys.urlPersonalizada) private var urlPersonalizada = ""

    @State private var modelos: [ModeloLLM] = []
    @State private var modelosEstaticos: Bool = true      // lista dinâmica falhou
    @State private var carregandoModelos: Bool = false
    @State private var status: String = "Verificando…"
    @State private var statusOk: Bool? = nil
    @State private var chave: String = KeychainHelper.ler() ?? ""
    @State private var geracao = 0  // descarta respostas atrasadas de providers antigos
    @FocusState private var focoChave: Bool      // FIX 4: salva key no blur, não a cada tecla
    @State private var chaveModificada = false   // FIX 4: evita re-salvar sem edição
    @State private var erroKeychain: String?     // FIX 4: alerta de persistência

    private var provider: LLMProvider { LLMProvider(rawValue: providerRaw) ?? .ollama }
    private var urlResolvida: String? {
        LLMProvider.urlResolvida(providerRaw: providerRaw, urlPersonalizada: urlPersonalizada)
    }
    private var chaveTratada: String? {
        chave.trimmingCharacters(in: .whitespaces).isEmpty ? nil : chave
    }

    private var opcoesModelo: [String] {
        var ids = modelos.map(\.id)
        if modelosEstaticos { ids = provider.modelosPadrao }
        // Modelo digitado manualmente sempre permanece selecionável
        let modeloAtual = modelo.trimmingCharacters(in: .whitespaces)
        if !modeloAtual.isEmpty && !ids.contains(modeloAtual) {
            ids.append(modeloAtual)
        }
        return ids
    }

    private var avisoVisao: String? {
        if provider.visaoPadrao == false {
            return "\(provider.nome) não tem visão — OCR de imagem fica só no Tesseract"
        }
        if let m = modelos.first(where: { $0.id == modelo }), m.visao == false {
            return "\(modelo) não tem visão — OCR de imagem fica só no Tesseract"
        }
        return nil
    }

    var body: some View {
        Form {
            Section("Provedor") {
                Picker("Provedor", selection: $providerRaw) {
                    ForEach(LLMProvider.allCases) { p in
                        Text(p.nome).tag(p.rawValue)
                    }
                }
                .onChange(of: providerRaw) { _ in
                    // Trocar provider atualiza a URL e limpa o modelo selecionado
                    modelo = ""
                    recarregar()
                }

                if provider == .personalizado {
                    TextField("URL base da API (compatível com OpenAI)", text: $urlPersonalizada)
                        .onChange(of: urlPersonalizada) { _ in recarregar() }
                }

                if provider.requerKey {
                    SecureField("API key", text: $chave)
                        .focused($focoChave)
                        .onChange(of: chave) { _ in chaveModificada = true }
                        // FIX 4: antes, cada keystroke fazia delete+add no
                        // Keychain (D7) e ignorava o retorno Bool — falha de
                        // persistência era silenciosa. Agora salva em commit
                        // (Enter) ou perda de foco, com alerta em falha.
                        .onSubmit { persistirChave() }
                        .onChange(of: focoChave) { temFoco in
                            if !temFoco { persistirChave() }
                        }
                }
            }

            Section("Modelo") {
                if carregandoModelos {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Carregando modelos…")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                Picker("Modelo", selection: $modelo) {
                    ForEach(opcoesModelo, id: \.self) { id in
                        Text(id).tag(id)
                    }
                }
                .disabled(opcoesModelo.isEmpty)

                TextField("Ou digite outro modelo…", text: $modelo)

                if modelosEstaticos && !carregandoModelos && urlResolvida != nil {
                    Text("Servidor não respondeu — lista estática. Confirme se o provedor está rodando.")
                        .font(.caption2)
                        .foregroundColor(.orange)
                }

                if let aviso = avisoVisao {
                    Text(aviso)
                        .font(.caption2)
                        .foregroundColor(.orange)
                }
            }

            Section("Conexão") {
                HStack(spacing: 8) {
                    Circle()
                        .fill(corStatus)
                        .frame(width: 8, height: 8)
                    Text(status)
                        .font(.callout)
                    Spacer()
                    Button("Testar") { verificarConexao(geracao: geracao + 1) }
                        .controlSize(.small)
                        .disabled(!conectavel)
                }
                if !configurado {
                    Text("Configure provedor e modelo para habilitar o LLM.")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 460)
        .padding(12)
        .task { recarregar() }
        // Alerta de falha de persistência da API key (FIX 4)
        .alert(
            "Erro ao salvar API key",
            isPresented: Binding(get: { erroKeychain != nil }, set: { if !$0 { erroKeychain = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(erroKeychain ?? "")
        }
    }

    /// Endpoint alcançável sem modelo escolhido. Lista de modelos e teste de
    /// conexão dependem só disto: exigir modelo aqui deixava a lista dinâmica
    /// presa na estática (trocar de provider zera o modelo).
    private var conectavel: Bool {
        LLMProvider.conectavel(
            providerRaw: providerRaw,
            urlPersonalizada: urlPersonalizada,
            chave: chaveTratada
        )
    }

    private var configurado: Bool {
        LLMProvider.configurado(
            providerRaw: providerRaw,
            urlPersonalizada: urlPersonalizada,
            chave: chaveTratada,
            modelo: modelo
        )
    }

    private var corStatus: Color {
        switch statusOk {
        case true: return .green
        case false: return .red
        case nil: return .gray
        }
    }

    /// Recarrega lista de modelos + status de conexão em paralelo.
    /// Cada gatilho incrementa a geração; respostas obsoletas são descartadas
    /// (trocar de provider duas vezes rápido não deixa o provider antigo
    /// sobrescrever a UI do novo).
    private func recarregar() {
        geracao += 1
        carregandoModelos = true
        carregarModelos(geracao: geracao)
        verificarConexao(geracao: geracao)
    }

    /// Persiste a key no Keychain (D7) em commit/blur — nunca a cada tecla.
    /// Campo esvaziado chama `apagar()`; falha do SecItemAdd vira alerta.
    private func persistirChave() {
        guard chaveModificada else { return }  // nada digitado desde o último save
        let valor = chave.trimmingCharacters(in: .whitespaces)
        if valor.isEmpty {
            KeychainHelper.apagar()
            erroKeychain = nil
            chaveModificada = false
        } else if KeychainHelper.salvar(valor) {
            erroKeychain = nil
            chaveModificada = false
        } else {
            erroKeychain = "Não foi possível salvar a API key no Keychain."
        }
        recarregar()
    }

    private func carregarModelos(geracao: Int) {
        guard let url = urlResolvida, conectavel else {
            modelos = []
            modelosEstaticos = true
            carregandoModelos = false
            return
        }
        Task {
            let dados = await LLMProbe.executar(["llm", "modelos", "--json"], url: url, key: chaveTratada)
            guard geracao == self.geracao else { return }  // resposta obsoleta
            if let dados,
               let resposta = try? JSONDecoder().decode(RespostaModelos.self, from: dados),
               resposta.ok {
                await MainActor.run {
                    guard geracao == self.geracao else { return }
                    modelos = resposta.modelos
                    modelosEstaticos = false
                    carregandoModelos = false
                }
            } else {
                await MainActor.run {
                    guard geracao == self.geracao else { return }
                    modelos = []
                    modelosEstaticos = true
                    carregandoModelos = false
                }
            }
        }
    }

    private func verificarConexao(geracao: Int) {
        guard let url = urlResolvida, conectavel else {
            status = "Sem configuração"
            statusOk = nil
            return
        }
        status = "Verificando…"
        statusOk = nil
        Task {
            let dados = await LLMProbe.executar(["llm", "testar", "--json"], url: url, key: chaveTratada)
            guard geracao == self.geracao else { return }  // resposta obsoleta
            if let dados,
               let resposta = try? JSONDecoder().decode(RespostaTeste.self, from: dados),
               resposta.ok,
               let latencia = resposta.latencia_ms {
                await MainActor.run {
                    guard geracao == self.geracao else { return }
                    status = "Conectado (\(latencia)ms)"
                    statusOk = true
                }
            } else {
                let detalhe = (try? JSONDecoder().decode(RespostaTeste.self, from: dados ?? Data()))?.erro
                await MainActor.run {
                    guard geracao == self.geracao else { return }
                    status = "Inacessível\(detalhe.map { " — \($0)" } ?? "")"
                    statusOk = false
                }
            }
        }
    }
}
