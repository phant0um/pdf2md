// KeychainHelper.swift
// Armazenamento da API key do LLM no Keychain (D7).
//
// UserDefaults seria um plist em claro dentro do container do app — a key
// precisa de kSecClassGenericPassword. A key é LIDA daqui no início da
// conversão e injetada no environment do processo Python (D8), nunca em
// argv (apareceria em `ps aux` para qualquer processo do mesmo usuário).
//
// Leitura em cache: a ACL do item no login keychain é amarrada à assinatura
// do app que o criou. Com assinatura que muda a cada build, CADA acesso pode
// disparar o diálogo de senha do macOS — e `ler()` era chamado a cada render
// do SwiftUI. Agora o Keychain é lido no máximo uma vez por execução; o cache
// é atualizado por `salvar()`/`apagar()`. Nome do serviço NÃO influencia a
// ACL — a correção da causa é assinatura estável (ver ADR-0007, build_app.sh).
import Foundation
import Security

enum KeychainHelper {
    static let servico = "com.pdf2md.llm"
    static let conta = "api-key"

    // Cache em memória: `.none` = ainda não lido; `.some(nil)` = lido, sem key.
    nonisolated(unsafe) private static var cache: String??  // protegido por `trava`
    private static let trava = NSLock()

    @discardableResult
    static func salvar(_ valor: String) -> Bool {
        guard let dados = valor.data(using: .utf8) else { return false }

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: servico,
            kSecAttrAccount as String: conta,
        ]
        // Substitui item existente (delete + add é mais confiável que update
        // quando o item anterior foi criado com outra política de acesso).
        SecItemDelete(query as CFDictionary)

        let attrs: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: servico,
            kSecAttrAccount as String: conta,
            kSecValueData as String: dados,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        let ok = SecItemAdd(attrs as CFDictionary, nil) == errSecSuccess
        trava.lock(); cache = .some(ok ? valor : nil); trava.unlock()
        return ok
    }

    /// Retorna a key do cache; toca o Keychain só na primeira chamada.
    static func ler() -> String? {
        trava.lock(); defer { trava.unlock() }
        if let valor = cache { return valor }
        let lido = lerDoKeychain()
        cache = .some(lido)
        return lido
    }

    private static func lerDoKeychain() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: servico,
            kSecAttrAccount as String: conta,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let dados = item as? Data else {
            return nil
        }
        return String(data: dados, encoding: .utf8)
    }

    static func apagar() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: servico,
            kSecAttrAccount as String: conta,
        ]
        SecItemDelete(query as CFDictionary)
        trava.lock(); cache = .some(nil); trava.unlock()
    }
}
