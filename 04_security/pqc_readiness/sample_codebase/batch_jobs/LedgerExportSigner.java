// SampleCo Finance -- signs the month-end general ledger export (synthetic).
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.Signature;
import java.security.interfaces.RSAPublicKey;
import javax.crypto.Cipher;
import javax.crypto.KeyAgreement;

public class LedgerExportSigner {
    public static void main(String[] args) throws Exception {
        KeyPairGenerator signer = KeyPairGenerator.getInstance("DSA");
        signer.initialize(2048);
        MessageDigest digest = MessageDigest.getInstance("SHA-1");
        KeyAgreement agreement = KeyAgreement.getInstance("DiffieHellman");
        Signature approval = Signature.getInstance("SHA256withECDSA");
        Cipher legacyVault = Cipher.getInstance("DESede/CBC/PKCS5Padding");
    }
}
