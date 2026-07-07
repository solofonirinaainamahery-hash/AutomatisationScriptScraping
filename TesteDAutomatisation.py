import datetime

# Écrit un message dans le terminal de GitHub
print("Le script de test s'exécute avec succès !")

# Crée ou met à jour un fichier pour prouver que la sauvegarde fonctionne
with open("historique_test.txt", "a") as f:
    f.write(f"Test réussi le : {datetime.datetime.now()}\n")
