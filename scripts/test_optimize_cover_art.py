#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Suite de Verificación para optimize_cover_art.py
"""

import os
import io
import shutil
import tempfile
import unittest
from PIL import Image

# Importar las clases del script
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
from optimize_cover_art import ImageOptimizer, AudioCoverManager, AlbumProcessor

class TestCoverArtOptimization(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_cover_art_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_image(self, size=(2000, 2000), mode='RGB', color=(100, 150, 200), fmt='JPEG'):
        img = Image.new(mode, size, color=color)
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_image_optimizer_oversized(self):
        """Verifica que una imagen de 2500x2500 sea reducida a 1200x1200 y <= 500KB."""
        raw_data = self._create_image(size=(2500, 2500), fmt='JPEG')
        w, h, size = ImageOptimizer.inspect(raw_data)
        self.assertEqual((w, h), (2500, 2500))

        opt_bytes, new_w, new_h, new_size = ImageOptimizer.optimize(raw_data, max_dim=1200, max_bytes=500*1024)
        self.assertLessEqual(new_w, 1200)
        self.assertLessEqual(new_h, 1200)
        self.assertLessEqual(new_size, 500 * 1024)
        self.assertTrue(ImageOptimizer.is_jpeg(opt_bytes))

    def test_image_optimizer_rgba_transparency(self):
        """Verifica que imágenes PNG con canal alfa se conviertan correctamente a JPEG con fondo blanco."""
        img = Image.new('RGBA', (1500, 1500), (255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        png_data = buf.getvalue()

        opt_bytes, new_w, new_h, new_size = ImageOptimizer.optimize(png_data, max_dim=1200, max_bytes=500*1024)
        self.assertLessEqual(new_w, 1200)
        self.assertLessEqual(new_size, 500 * 1024)
        self.assertTrue(ImageOptimizer.is_jpeg(opt_bytes))

    def test_album_processor_rules(self):
        """Prueba las 3 reglas del ciclo de vida de un álbum."""
        processor = AlbumProcessor(max_dim=1200, max_bytes=500*1024, quality=85, dry_run=False)

        # 1. Caso OMITIDO: carpeta vacía de audio
        res = processor.process_folder(self.test_dir)
        self.assertEqual(res['status'], 'OMITIDO')

        # Crear un archivo de audio simulado (archivo vacío o FLAC si mutagen lo soporta)
        # Vamos a probar la regla con una imagen en disco
        # Caso A: imagen sobredimensionada en disco folder.jpg
        cand_path = os.path.join(self.test_dir, 'folder.jpg')
        with open(cand_path, 'wb') as f:
            f.write(self._create_image(size=(1800, 1800)))

        # Crear un archivo dummy con extensión de audio
        dummy_audio = os.path.join(self.test_dir, 'track01.mp3')
        with open(dummy_audio, 'wb') as f:
            f.write(b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\x00'*100)

        res = processor.process_folder(self.test_dir)
        self.assertEqual(res['status'], 'OPTIMIZADO')
        cover_path = os.path.join(self.test_dir, 'cover.jpg')
        self.assertTrue(os.path.exists(cover_path))
        with open(cover_path, 'rb') as f:
            w, h, sz = ImageOptimizer.inspect(f.read())
        self.assertLessEqual(w, 1200)
        self.assertLessEqual(h, 1200)
        self.assertLessEqual(sz, 500 * 1024)

        # Caso B: Ya optimizado y cover.jpg existe -> debe ser OMITIDO
        res2 = processor.process_folder(self.test_dir)
        self.assertEqual(res2['status'], 'OMITIDO')

        # Caso C: Imagen cumple límites pero NO existe cover.jpg (solo folder.jpg)
        os.remove(cover_path)
        with open(cand_path, 'wb') as f:
            f.write(self._create_image(size=(800, 800)))
        res3 = processor.process_folder(self.test_dir)
        self.assertEqual(res3['status'], 'EXTRAÍDO')
        self.assertTrue(os.path.exists(cover_path))


if __name__ == '__main__':
    unittest.main()
