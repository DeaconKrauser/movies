import os
import requests
from bs4 import BeautifulSoup
import libtorrent as lt
import time
import sqlite3
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaFileUpload
import re
import shutil
from dotenv import load_dotenv

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')
PARENT_FOLDER_ID = os.getenv('PARENT_FOLDER_ID')

WAIT_FOR_SEED_SECONDS = 1

def authenticate():
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return creds

def upload_movie_google_drive(file_path, torrent_name):
    try:
        creds = authenticate()
        service = build('drive', 'v3', credentials=creds)
        
        # Obter o nome do arquivo a partir do caminho do arquivo
        movie_name = os.path.basename(file_path)
        safe_movie_name = re.sub(r'[<>:"/\\|?*]', '', movie_name)
        print('filme pego:', movie_name)
        # Usar o nome do arquivo do torrent para o arquivo no Google Drive
        file_metadata = {
            'name': f"{torrent_name}.mkv",
            'parents': [PARENT_FOLDER_ID]
        }
        print('nome sendo salvo:', file_metadata)
        media = MediaFileUpload(file_path, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media).execute()
        print(f"Upload para o Google Drive concluído. ID do arquivo: {file['id']}")

    except Exception as e:
        print(f"Erro durante o upload para o Google Drive: {e}")

def criar_tabela():
    conn = sqlite3.connect('filmes.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filmes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE,
            magnet_link TEXT
        )
    ''')

    conn.commit()
    conn.close()

def inserir_filme(nome, magnet_link):
    conn = sqlite3.connect('filmes.db')
    cursor = conn.cursor()

    cursor.execute('INSERT OR IGNORE INTO filmes (nome, magnet_link) VALUES (?, ?)', (nome, magnet_link))

    conn.commit()
    conn.close()

def filme_baixado(nome):
    conn = sqlite3.connect('filmes.db')
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM filmes WHERE nome = ?', (nome,))
    resultado = cursor.fetchone()

    conn.close()

    return resultado is not None

def buscar_filmes_por_palavra_chave(categoria, palavra_chave):
    criar_tabela()

    filmes_encontrados = []

    for pagina in range(1, 83):  # Considerando que existem 82 páginas
        url_pagina = f"https://baixarfilmesbr.net/category/{categoria}/page/{pagina}/"
        filmes_na_pagina = buscar_filmes_na_pagina(url_pagina, palavra_chave)

        filmes_encontrados.extend(filmes_na_pagina)

    if not filmes_encontrados:
        print("Nenhum filme encontrado com a palavra-chave fornecida.")
        return

    print("Filmes encontrados:")
    for i, filme in enumerate(filmes_encontrados, start=1):
        print(f"{i}. Nome do Filme: {filme['nome']}")
        print(f"   Link Magnético: {filme['magnet_link']}")
        print("=" * 30)

    while True:
        try:
            escolha = int(input("Digite o número do filme que deseja baixar (ou 0 para sair): "))
            if escolha == 0:
                break

            filme_escolhido = filmes_encontrados[escolha - 1]

            if filme_baixado(filme_escolhido['nome']):
                print(f"Este filme já foi baixado anteriormente.")
                escolha_outro = input("Deseja baixar outro filme? (s/n): ").lower()
                if escolha_outro != 's':
                    break
                else:
                    continue

            download_libtorrent(filme_escolhido['nome'], filme_escolhido['magnet_link'])
            inserir_filme(filme_escolhido['nome'], filme_escolhido['magnet_link'])

            # Continuar baixando mais filmes
            continuar = input("Deseja baixar mais filmes? (s/n): ").lower()
            if continuar != 's':
                break

        except (ValueError, IndexError):
            print("Escolha inválida. Tente novamente.")

def buscar_filmes_na_pagina(url, palavra_chave):
    try:
        response = requests.get(url)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')

            links = []
            for link_tag in soup.find_all('a', href=True):
                descricao = link_tag.get_text(strip=True)
                url_link = link_tag['href']
                links.append({'descricao': descricao, 'url': url_link})

            filmes_encontrados = []
            for link in links:
                if palavra_chave.lower() in link['descricao'].lower():
                    magnet_link = obter_magnet_link(link['url'])
                    if magnet_link:
                        nome_filme = extrair_nome_filme(link['descricao'])
                        filmes_encontrados.append({'nome': nome_filme, 'magnet_link': magnet_link})

            return filmes_encontrados

        else:
            print(f"Erro ao acessar a URL. Código de status: {response.status_code}")

    except requests.RequestException as e:
        print(f"Erro durante a execução do requests: {e}")
        return []

def obter_magnet_link(url):
    try:
        response = requests.get(url)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')

            magnet_link_tag = soup.find('a', class_='botao', href=lambda x: x and x.startswith('magnet:'))
            
            if magnet_link_tag:
                return magnet_link_tag['href']

        else:
            print(f"Erro ao acessar a URL. Código de status: {response.status_code}")

    except requests.RequestException as e:
        print(f"Erro durante a execução do requests: {e}")

    return None

def extrair_nome_filme(descricao):
    nome_filme = descricao.split(' Torrent')[0].split('- download')[0].strip()
    return nome_filme

def download_libtorrent(nome_filme, magnet_link):
    try:
        save_path = '/home/otavio/filmes_temp'
        max_wait_time = 300  # 5 minutos em segundos
        wait_interval = 5  # 5 segundos

        if filme_baixado(nome_filme):
            print(f"Este filme já foi baixado anteriormente.")
            return

        ses = lt.session()
        params = {
            'save_path': save_path,
            'storage_mode': lt.storage_mode_t(2),
        }

        handle = lt.add_magnet_uri(ses, magnet_link, params)

        print(f"Baixando: {handle.status().name}")

        wait_time = 0
        while not handle.status().is_seeding:
            s = handle.status()
            progress = s.progress * 100

            print(f"Progresso: {progress:.2f}%")
            time.sleep(WAIT_FOR_SEED_SECONDS)

            # Verificar se o progresso ainda é zero após 5 minutos
            if progress == 0 and wait_time >= max_wait_time:
                print("Tempo limite excedido. O torrent não pôde ser baixado.")
                return

            wait_time += wait_interval

        print("Download concluído!")

        # Obter o nome do arquivo após o download
        nome_arquivo_downloaded = handle.get_torrent_info().files().file_path(0)

        # Caminho completo do arquivo no diretório de download temporário
        caminho_temp = os.path.join(save_path, nome_arquivo_downloaded)

        # Verificar se o arquivo existe antes de tentar fazer o upload
        if os.path.exists(caminho_temp):
            # Após o download, autenticar e fazer o upload do filme para o Google Drive
            upload_movie_google_drive(caminho_temp, nome_filme)

            # Remover o arquivo após o upload
            os.remove(caminho_temp)

    except Exception as e:
        print(f"Erro durante o download com libtorrent: {e}")

    finally:
        # Remover a pasta temporária após o upload
        caminho_temp = os.path.join(save_path, handle.name())
        print(caminho_temp)
        if os.path.exists(caminho_temp):
            shutil.rmtree(caminho_temp)

# Exemplo de uso
categoria_filmes = "filmes"
palavra_chave = input("Digite a palavra-chave ou o nome do filme: ")
buscar_filmes_por_palavra_chave(categoria_filmes, palavra_chave)
