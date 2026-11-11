// LLMConfig.swift
// Configuração do LLM (provedor/modelo/URL) para a GUI.
//
// Espelha o docstring de core/llm_enhancer.py:
//   Ollama       http://localhost:11434/v1            (sem key)
//   Gemini       https://generativelanguage.googleapis.com/v1beta/openai/
//   Groq         https://api.groq.com/openai/v1
//   OpenRouter   https://openrouter.ai/api/v1
//   OpenCode Zen https://opencode.ai/zen/v1
//   OpenCode Go  https://opencode.ai/zen/go/v1
//   Ollama Cloud https://ollama.com/v1
//
// Precedência de resolução da URL: personalizado (campo livre) > preset.
// O modelo é sincronizado com o Python via PDF2MD_LLM_MODEL (environment),
// nunca via argv — ver BatchProcessor.swift (D8).
import Foundation

// Presets de provider — a URL base e a necessidade de key são decisões
// do provedor, não do documento. Swift só faz parse do JSON do binário.
enum LLMProvider: String, CaseIterable, Identifiable {
    case ollama
    case gemini
    case groq
    case openrouter
    case opencodeZen
    case opencodeGo
    case ollamaCloud
    case personalizado

    var id: String { rawValue }

    var nome: String {
        switch self {
        case .ollama: return "Ollama (local)"
        case .gemini: return "Gemini"
        case .groq: return "Groq"
        case .openrouter: return "OpenRouter"
        case .opencodeZen: return "OpenCode Zen"
        case .opencodeGo: return "OpenCode Go"
        case .ollamaCloud: return "Ollama Cloud"
        case .personalizado: return "Personalizado…"
        }
    }

    // nil = "Personalizado…" exige URL digitada pelo usuário
    var urlBase: String? {
        switch self {
        case .ollama: return "http://localhost:11434/v1"
        case .gemini: return "https://generativelanguage.googleapis.com/v1beta/openai/"
        case .groq: return "https://api.groq.com/openai/v1"
        case .openrouter: return "https://openrouter.ai/api/v1"
        case .opencodeZen: return "https://opencode.ai/zen/v1"
        case .opencodeGo: return "https://opencode.ai/zen/go/v1"
        case .ollamaCloud: return "https://ollama.com/v1"
        case .personalizado: return nil
        }
    }

    var requerKey: Bool {
        switch self {
        case .ollama: return false  // Ollama local não valida key
        default: return true        // nuvem (inclui OpenCode e Ollama Cloud)
        }
    }

    // true/false = definitivo; nil = depende do modelo instalado
    var visaoPadrao: Bool? {
        switch self {
        case .gemini: return true
        case .groq: return false
        default: return nil
        }
    }

    // Lista estática mínima usada quando `llm modelos` falha (sem servidor)
    var modelosPadrao: [String] {
        switch self {
        case .ollama: return ["llama3.2-vision", "llama3.1:8b", "qwen2.5"]
        case .gemini: return ["gemini-2.0-flash"]
        case .groq: return ["llama-3.1-8b-instant"]
        case .openrouter: return ["anthropic/claude-3.5-sonnet"]
        case .opencodeZen: return ["deepseek-v4-flash", "deepseek-v4-pro", "kimi-k2.6", "glm-5.2"]
        case .opencodeGo: return ["grok-4.5", "kimi-k3", "deepseek-v4-pro", "deepseek-v4-flash"]
        case .ollamaCloud: return ["gpt-oss:120b", "glm-5:cloud", "kimi-k2.6"]
        case .personalizado: return []
        }
    }

    /// Resolve a URL efetiva: preset ou campo personalizado (nil se vazio).
    static func urlResolvida(providerRaw: String, urlPersonalizada: String) -> String? {
        let provider = LLMProvider(rawValue: providerRaw) ?? .ollama
        if provider == .personalizado {
            let custom = urlPersonalizada.trimmingCharacters(in: .whitespaces)
            return custom.isEmpty ? nil : custom
        }
        return provider.urlBase
    }

    /// True quando dá para falar com o endpoint: URL http(s) válida + key
    /// presente quando o provider exige. Não exige modelo — listar modelos e
    /// testar a conexão vêm ANTES da escolha do modelo.
    static func conectavel(providerRaw: String, urlPersonalizada: String, chave: String?) -> Bool {
        guard let url = urlResolvida(providerRaw: providerRaw, urlPersonalizada: urlPersonalizada),
              url.hasPrefix("http://") || url.hasPrefix("https://") else {
            return false
        }
        let provider = LLMProvider(rawValue: providerRaw) ?? .ollama
        if provider.requerKey {
            let k = chave ?? ""
            return !k.trimmingCharacters(in: .whitespaces).isEmpty
        }
        return true
    }

    /// True quando há configuração suficiente para converter com LLM (D10).
    /// "Configurado" = endpoint conectável + MODELO selecionado — sem modelo,
    /// o Python cairia no default llama3.2-vision (404 em Groq/Gemini) e o
    /// toggle ligaria sem efeito.
    static func configurado(providerRaw: String, urlPersonalizada: String, chave: String?, modelo: String) -> Bool {
        conectavel(providerRaw: providerRaw, urlPersonalizada: urlPersonalizada, chave: chave)
            && !modelo.trimmingCharacters(in: .whitespaces).isEmpty
    }
}

// Chaves do UserDefaults (@AppStorage) — @AppStorage é o caminho oficial
// para preferências simples; a API key NUNCA fica aqui (ver KeychainHelper).
enum LLMDefaultsKeys {
    static let provider = "llm.provider"
    static let modelo = "llm.modelo"
    static let urlPersonalizada = "llm.urlPersonalizada"
}
