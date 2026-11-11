// ContentView.swift
// Interface principal: drag-drop + configurações + progresso
import Combine
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var processador = BatchProcessor()
    @State private var arquivosSelecionados: [URL] = []
    @State private var caminho: URL?          // único campo: pasta saída ou vault
    @State private var modoObsidian: Bool = false
    @State private var modoLLM: Bool = false      // ⚡ LLM fallback (Ollama/Gemini)
    @State private var isDragOver: Bool = false
    @State private var tarefaConversao: Task<Void, Never>?
    @State private var erroColagem: String? = nil  // nil = sem alert; non-nil = mensagem exibida
    // Espelho observável da key do Keychain: troca só da chave nas Preferências
    // reavalia `llmConfigurado` (o cache do KeychainHelper não é observável).
    @State private var chaveLLM: String? = KeychainHelper.ler()

    // Config do LLM (Preferências) — @AppStorage espelha SettingsView
    @AppStorage(LLMDefaultsKeys.provider) private var llmProviderRaw = LLMProvider.ollama.rawValue
    @AppStorage(LLMDefaultsKeys.modelo) private var llmModelo = ""
    @AppStorage(LLMDefaultsKeys.urlPersonalizada) private var llmUrlPersonalizada = ""

    /// True quando há provider + key + modelo suficientes para o LLM (D10).
    private var llmConfigurado: Bool {
        LLMProvider.configurado(
            providerRaw: llmProviderRaw,
            urlPersonalizada: llmUrlPersonalizada,
            chave: chaveLLM,
            modelo: llmModelo
        )
    }

    /// Toggle efetivamente ativo: ligado e com config válida.
    private var llmAtivo: Bool { modoLLM && llmConfigurado }

    // Piso da lista de arquivos (~5 linhas). Sem teto: a lista é elástica e
    // absorve todo o espaço livre da janela.
    private static let alturaMinimaLista: CGFloat = 160
    // Zona de arrastar: expande quando não há arquivos (ocupa o espaço que a
    // lista ainda não usa e aumenta o alvo de drop), encolhe quando há.
    private static let alturaArrastarVazia: CGFloat = 280
    private static let alturaArrastarCompacta: CGFloat = 110

    // Extensões suportadas — espelha core/utils.py EXTENSOES_PERMITIDAS
    // (fonte da verdade; o core valida por path.suffix.lower()).
    private static let extensoesPermitidas: Set<String> = [
        "pdf", "png", "jpg", "jpeg", "tiff", "tif", "webp", "bmp", "heic",
        "doc", "docx", "pptx", "xlsx", "csv",
    ]

    // Tipos permitidos no picker — derivados das EXTENSÕES (não de UTIs
    // hardcoded): o LaunchServices resolve .docx/.pptx/.xlsx para UTIs SEM
    // o segmento "officedocument." (ex: org.openxmlformats.wordprocessingml
    // .document) — a UTI hardcoded antiga (com "officedocument.") nunca
    // casava, e o drop/picker rejeitava docx, pptx e xlsx.
    private static let tiposPermitidos: [UTType] = {
        extensoesPermitidas.compactMap { UTType(filenameExtension: $0) }
    }()

    // Pasta temporária para imagens coladas — compartilhada entre colarImagem e limpar
    private var pasteDir: URL {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("pdf2md/pastes")
    }

    // Label e picker mudam conforme modo
    private var labelCaminho: String {
        modoObsidian ? "Vault Obsidian" : "Pasta de saída"
    }
    private var placeholderCaminho: String {
        modoObsidian ? "Selecionar vault…" : "Selecionar pasta…"
    }
    private var mensagemPicker: String {
        modoObsidian ? "Selecione a raiz do vault Obsidian" : "Selecione a pasta de saída"
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header com logo
            cabecalho

            Divider()

            // Topo fixo: zona de drop + botões de adicionar
            VStack(spacing: 16) {
                zonaArrastar
                    .padding(.top, 16)

                botoesAdicionarArquivos
            }
            .padding(.horizontal, 16)

            // Lista de arquivos — área elástica: absorve todo o espaço que sobra
            // e cresce ao redimensionar a janela. A List rola internamente, então
            // não precisa de ScrollView externo (que a limitaria à altura intrínseca).
            if !arquivosSelecionados.isEmpty {
                listaArquivos
                    .padding(.horizontal, 16)
                    .padding(.top, 16)
            } else {
                Spacer(minLength: 0)
            }

            // Rodapé ancorado: os controles de conversão nunca saem da tela,
            // independente de quantos arquivos estejam na lista.
            VStack(spacing: 16) {
                // Saída / Vault (campo unificado)
                campoCaminho

                // Toggles: Obsidian + LLM fallback
                GroupBox {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("Modo Obsidian", isOn: $modoObsidian)
                            .onChange(of: modoObsidian) { _ in
                                caminho = nil  // limpa ao trocar modo
                            }
                            .disabled(processador.estaProcessando)

                        Divider()

                        Toggle(isOn: $modoLLM) {
                            VStack(alignment: .leading, spacing: 2) {
                                Label("⚡ Melhorar com IA (fallback)", systemImage: "sparkles")
                                    .font(.body)
                                Text(llmConfigurado
                                     ? "Usa o provedor configurado quando a qualidade está baixa."
                                     : "Configure provedor, modelo e chave nas Preferências…")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .disabled(processador.estaProcessando || !llmConfigurado)
                        .onChange(of: llmConfigurado) { configurado in
                            // Config ficou incompleta: desliga o toggle em vez de deixá-lo ligado e desabilitado
                            if !configurado { modoLLM = false }
                        }

                        if !llmConfigurado {
                            Button("Abrir Preferências…") {
                                // macOS 13: abre a scene `Settings`
                                NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                            }
                            .buttonStyle(.link)
                            .controlSize(.small)
                        }
                    }
                }

                // Botões de ação
                botoesAcao

                // Progresso
                if processador.estaProcessando {
                    barraProgresso
                    // Feedback de tempo: conversão longa (OCR de scans) não
                    // muda o contador de arquivos por minutos — o relógio
                    // mostra que o app está vivo e trabalhando.
                    if let inicio = processador.inicioConversao {
                        TimelineView(.periodic(from: .now, by: 1)) { contexto in
                            Text("Processando… \(BatchProcessor.formatarDuracao(contexto.date.timeIntervalSince(inicio)))")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }

                // Tempo total ao concluir
                if processador.concluido, let t = processador.duracaoTotal {
                    Text("Concluído em \(BatchProcessor.formatarDuracao(t))")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 16)
            .padding(.bottom, 16)
        }
        // Altura mínima cobre topo + rodapé fixos + uma lista utilizável.
        .frame(minWidth: 480, minHeight: 640)
        // Key salva/apagada nas Preferências: atualiza o espelho e reavalia llmConfigurado
        .onReceive(NotificationCenter.default.publisher(for: KeychainHelper.mudou).receive(on: RunLoop.main)) { _ in
            chaveLLM = KeychainHelper.ler()
        }
        // Alert com mensagem dinâmica — distingue clipboard vazio de erro de I/O
        .alert(
            "Erro ao colar imagem",
            isPresented: Binding(get: { erroColagem != nil }, set: { if !$0 { erroColagem = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(erroColagem ?? "")
        }
        // Alerta de erro fatal do processamento (FIX 2): binário ausente do
        // bundle, destino/vault fora do home — falhas que antes ficavam só
        // no console e deixavam a UI sem feedback.
        .alert(
            "Erro",
            isPresented: Binding(get: { processador.erroFatal != nil }, set: { if !$0 { processador.erroFatal = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(processador.erroFatal ?? "")
        }
    }

    // MARK: — Subviews

    private var cabecalho: some View {
        HStack(spacing: 10) {
            Image("AppIcon")
                .resizable()
                .frame(width: 32, height: 32)
                .clipShape(RoundedRectangle(cornerRadius: 7))
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private var botoesAdicionarArquivos: some View {
        HStack(spacing: 8) {
            Button {
                adicionarArquivos()
            } label: {
                Label("Procurar arquivos…", systemImage: "folder.badge.plus")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(processador.estaProcessando)

            Button {
                colarImagem()
            } label: {
                Label("Colar imagem", systemImage: "doc.on.clipboard")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(processador.estaProcessando)
            // Sem .keyboardShortcut("v") — evita interceptar Cmd+V de text fields futuros

            Spacer()
        }
    }

    private var alturaZonaArrastar: CGFloat {
        arquivosSelecionados.isEmpty ? Self.alturaArrastarVazia : Self.alturaArrastarCompacta
    }

    private var zonaArrastar: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(
                    isDragOver ? Color.accentColor : Color.secondary.opacity(0.4),
                    style: StrokeStyle(lineWidth: 2, dash: [6])
                )
                .background(
                    RoundedRectangle(cornerRadius: 12)
                        .fill(isDragOver ? Color.accentColor.opacity(0.08) : Color.clear)
                )
                .frame(height: alturaZonaArrastar)

            VStack(spacing: 6) {
                Image(systemName: "doc.badge.arrow.up")
                    .font(.system(size: 32))
                    .foregroundColor(isDragOver ? .accentColor : .secondary)
                Text("Arraste PDFs e imagens aqui")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                Text("PDF · DOCX · DOC · PPTX · XLSX · CSV · PNG · JPG · TIFF · WEBP · BMP · HEIC")
                    .font(.caption2)
                    .foregroundColor(.secondary.opacity(0.6))
            }
        }
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $isDragOver) { providers in
            handleDrop(providers: providers)
            return true
        }
    }

    private var listaArquivos: some View {
        GroupBox {
            List(arquivosSelecionados, id: \.self) { url in
                HStack {
                    statusIcon(for: url)
                    Text(url.lastPathComponent)
                        .lineLimit(1)
                        .font(.system(.body, design: .monospaced))
                    Spacer()
                    if let prog = processador.progresso.first(where: { $0.id == url.path }) {
                        if let err = prog.erro {
                            Text(err)
                                .font(.caption)
                                .foregroundColor(.red)
                                .lineLimit(1)
                        } else if let aviso = prog.avisos.first {
                            Text("⚠ \(aviso)")
                                .font(.caption2)
                                .foregroundColor(.orange)
                                .lineLimit(1)
                        }
                    }
                }
            }
            .listStyle(.plain)
            // Elástica: nunca menor que ~5 linhas, cresce até ocupar todo o espaço
            // livre da janela. Sem teto fixo — redimensionar a janela aumenta a lista.
            .frame(minHeight: Self.alturaMinimaLista, maxHeight: .infinity)
        }
    }

    private var campoCaminho: some View {
        GroupBox(labelCaminho) {
            HStack {
                Label(
                    caminho?.lastPathComponent ?? placeholderCaminho,
                    systemImage: modoObsidian ? "diamond" : "folder"
                )
                .foregroundColor(caminho == nil ? .secondary : .primary)
                .lineLimit(1)
                Spacer()
                Button("Escolher…") {
                    selecionarCaminho()
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
    }

    private var botoesAcao: some View {
        HStack(spacing: 12) {
            if processador.estaProcessando {
                // Botão Cancelar — visível apenas durante conversão
                Button(role: .destructive) {
                    cancelarConversao()
                } label: {
                    Label("Cancelar", systemImage: "xmark.circle")
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .keyboardShortcut(.escape, modifiers: [])
            }

            Button("Converter") {
                iniciarConversao()
            }
            .disabled(
                arquivosSelecionados.isEmpty ||
                caminho == nil ||
                processador.estaProcessando
            )
            .buttonStyle(.borderedProminent)
            .controlSize(.large)

            if processador.concluido {
                Button("Limpar") {
                    limpar()
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
        }
    }

    private var barraProgresso: some View {
        let total = processador.progresso.count
        let terminados = processador.progresso.filter {
            $0.status == "concluido" || $0.status == "erro" ||
            $0.status == "cancelado" || $0.status == "ignorado"
        }.count
        return VStack(spacing: 4) {
            ProgressView(value: Double(terminados), total: Double(max(total, 1)))
                .progressViewStyle(.linear)
            Text("\(terminados) / \(total)")
                .font(.caption)
                .foregroundColor(.secondary)
        }
    }

    // MARK: — Helpers

    private func statusIcon(for url: URL) -> some View {
        let progresso = processador.progresso.first { $0.id == url.path }
        switch progresso?.status {
        case "concluido":
            // Âmbar se há avisos de qualidade, verde se output limpo
            let temAvisos = !(progresso?.avisos.isEmpty ?? true)
            return Image(systemName: "checkmark.circle.fill")
                .foregroundColor(temAvisos ? .orange : .green)
        case "ignorado":
            return Image(systemName: "equal.circle.fill").foregroundColor(.secondary)
        case "erro":
            return Image(systemName: "xmark.circle.fill").foregroundColor(.red)
        case "cancelado":
            return Image(systemName: "minus.circle.fill").foregroundColor(.orange)
        case "processando":
            return Image(systemName: "arrow.2.circlepath").foregroundColor(.accentColor)
        default:
            return Image(systemName: "clock").foregroundColor(.secondary)
        }
    }

    // Adiciona URL à fila se ainda não estiver presente — dedup centralizado
    private func adicionarSeNovo(_ url: URL) {
        let seguro = url.resolvingSymlinksInPath()
        if !arquivosSelecionados.contains(seguro) {
            arquivosSelecionados.append(seguro)
        }
    }

    private func adicionarArquivos() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.message = "Selecione arquivos para converter"
        panel.allowedContentTypes = Self.tiposPermitidos
        // Garante que o panel apareça na frente em cenários multi-janela (macOS 14+)
        NSApp.activate(ignoringOtherApps: true)
        if panel.runModal() == .OK {
            panel.urls.forEach { adicionarSeNovo($0) }
        }
    }

    private func colarImagem() {
        erroColagem = nil  // reset garante re-disparo do alert em falhas consecutivas
        let pasteboard = NSPasteboard.general
        guard let items = pasteboard.readObjects(forClasses: [NSImage.self], options: nil) as? [NSImage],
              let imagem = items.first else {
            erroColagem = "Nenhuma imagem no clipboard."
            return
        }
        // Mesmo diretório que limpar() varre — fonte única em `pasteDir`.
        let dir = pasteDir
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            // UUID garante unicidade — sem colisão por segundo-granularity
            let destino = dir.appendingPathComponent("paste-\(UUID().uuidString).png")
            // Tenta TIFF→PNG; fallback CGImage para formatos vetoriais (PDF, SVG no clipboard)
            let pngData: Data
            if let tiff = imagem.tiffRepresentation,
               let rep = NSBitmapImageRep(data: tiff),
               let png = rep.representation(using: .png, properties: [:]) {
                pngData = png
            } else if let cgImg = imagem.cgImage(forProposedRect: nil, context: nil, hints: nil) {
                let rep = NSBitmapImageRep(cgImage: cgImg)
                guard let png = rep.representation(using: .png, properties: [:]) else {
                    erroColagem = "Não foi possível converter a imagem do clipboard."
                    return
                }
                pngData = png
            } else {
                erroColagem = "Não foi possível converter a imagem do clipboard."
                return
            }
            try pngData.write(to: destino)
            arquivosSelecionados.append(destino)  // UUID = sempre único, sem contains check
        } catch {
            erroColagem = "Erro ao salvar imagem: \(error.localizedDescription)"
        }
    }

    private func handleDrop(providers: [NSItemProvider]) {
        for provider in providers {
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                guard let data = item as? Data,
                      let url = URL(dataRepresentation: data, relativeTo: nil),
                      Self.tipoSuportado(url) else {
                    return
                }
                DispatchQueue.main.async {
                    self.adicionarSeNovo(url)
                }
            }
        }
    }

    /// FIX 5: drop aceitava qualquer UTType.fileURL (.app, .dmg… entravam na
    /// lista e viravam "ignorado"). Filtra por EXTENSÃO (case-insensitive) —
    /// comparação por UTI falhava: LaunchServices resolve .docx/.pptx/.xlsx
    /// para UTIs diferentes das hardcoded (ver tiposPermitidos acima).
    private static func tipoSuportado(_ url: URL) -> Bool {
        extensoesPermitidas.contains(url.pathExtension.lowercased())
    }

    private func selecionarCaminho() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = mensagemPicker
        if panel.runModal() == .OK {
            caminho = panel.url?.resolvingSymlinksInPath()
        }
    }

    private func iniciarConversao() {
        guard let destino = caminho else { return }
        tarefaConversao = Task {
            await processador.iniciarConversao(
                arquivos: arquivosSelecionados,
                destino: modoObsidian ? nil : destino,
                vault: modoObsidian ? destino : nil,
                obsidian: modoObsidian,
                // defesa: nunca manda --llm-fallback com config incompleta
                llmFallback: llmAtivo,
                llmURL: LLMProvider.urlResolvida(
                    providerRaw: llmProviderRaw,
                    urlPersonalizada: llmUrlPersonalizada
                ),
                llmModelo: llmModelo.trimmingCharacters(in: .whitespaces).isEmpty
                    ? nil : llmModelo,
                llmKey: KeychainHelper.ler()
            )
        }
    }

    private func cancelarConversao() {
        // O ProcessRunner escuta o cancelamento da task: SIGTERM + fecha
        // pipes + SIGKILL — a espera destrava na hora (ver ProcessRunner).
        tarefaConversao?.cancel()
    }

    private func limpar() {
        // Deleta arquivos temporários de paste antes de limpar a lista
        let dir = pasteDir.path + "/"
        for url in arquivosSelecionados where url.path.hasPrefix(dir) {
            try? FileManager.default.removeItem(at: url)
        }
        arquivosSelecionados.removeAll()
        caminho = nil
        modoObsidian = false
        processador.limpar()
        tarefaConversao = nil
    }
}
