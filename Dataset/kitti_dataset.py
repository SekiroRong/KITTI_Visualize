# -*- coding = utf-8 -*-
# @Time : 28/10/2022 δΈε4:59
# @Author : ζζ±
# @File : kitti_dataset.py
# @Software : PyCharm
# @Contact : sekirorong@gmail.com
# @github : https://github.com/SekiroRong
import sys
import os
import math
from builtins import int

import numpy as np
from torch.utils.data import Dataset
import cv2
import torch
from tqdm import tqdm

from data_process.kitti_velodyne_utils import makeBEVMap, drawRotatedBox, get_corners, makeFVMap, gen_hm_radius, compute_radius, Calibration, get_filtered_lidar, filter_fov_lidar
import transform_utils as transformation
import kitti_config as cnf


class KittiDataset(Dataset):
    def __init__(self, configs, mode='train', lidar_aug=None, hflip_prob=None, num_samples=None):
        self.dataset_dir = configs.dataset_dir
        self.input_size = configs.input_size
        self.hm_size = configs.hm_size

        self.num_classes = configs.num_classes
        self.max_objects = configs.max_objects

        assert mode in ['train', 'val', 'test'], 'Invalid mode: {}'.format(mode)
        self.mode = mode
        self.is_test = (self.mode == 'test')
        sub_folder = 'training' if self.is_test else 'training'

        self.lidar_aug = lidar_aug
        self.hflip_prob = hflip_prob

        self.image_dir = os.path.join(self.dataset_dir, sub_folder, "image_2")
        self.lidar_dir = os.path.join(self.dataset_dir, sub_folder, "velodyne_gt")
        self.pred_dir = os.path.join(self.dataset_dir, "result_full")
        self.calib_dir = os.path.join(self.dataset_dir, sub_folder, "calib")
        self.label_dir = os.path.join(self.dataset_dir, sub_folder, "label_2_raw")
        split_txt_path = os.path.join(self.dataset_dir, 'ImageSets', '{}.txt'.format(mode))
        if mode == 'test':
            split_txt_path = os.path.join(self.dataset_dir, 'ImageSets', 'val.txt')
            print('testing')

        self.sample_id_list = [int(x.strip()) for x in open(split_txt_path).readlines()]

        if num_samples is not None:
            self.sample_id_list = self.sample_id_list[:num_samples]
        self.num_samples = len(self.sample_id_list)

    def __len__(self):
        return len(self.sample_id_list)

    def __getitem__(self, index):
        if self.is_test:
            return self.load_img_only(index)
        else:
            return self.load_img_with_targets(index)

    def load_img_only(self, index):
        """Load only image for the testing phase"""
        sample_id = int(self.sample_id_list[index])
        img_path, img_rgb = self.get_image(sample_id)
        lidarData = self.get_lidar(sample_id)
        lidarData = filter_fov_lidar(lidarData)
        lidarData = get_filtered_lidar(lidarData, cnf.boundary)
        bev_map = makeBEVMap(lidarData, cnf.boundary)
        bev_map = torch.from_numpy(bev_map)

        metadatas = {
            'img_path': img_path,
        }

        return metadatas, bev_map, img_rgb

    def load_img_with_targets(self, index):
        """Load images and targets for the training and validation phase"""
        sample_id = int(self.sample_id_list[index])
        img_path = os.path.join(self.image_dir, '{:06d}.png'.format(sample_id))
        lidarData = self.get_lidar(sample_id)
        lidarData = filter_fov_lidar(lidarData)
        calib = self.get_calib(sample_id)
        labels, has_labels = self.get_label(sample_id)
        if has_labels:
            labels[:, 1:] = transformation.camera_to_lidar_box(labels[:, 1:], calib.V2C, calib.R0, calib.P2)

        if self.lidar_aug:
            lidarData, labels[:, 1:] = self.lidar_aug(lidarData, labels[:, 1:])

        lidarData, labels = get_filtered_lidar(lidarData, cnf.boundary, labels)

        bev_map = makeBEVMap(lidarData, cnf.boundary)
        bev_map = torch.from_numpy(bev_map)

        hflipped = False
        if np.random.random() < self.hflip_prob:
            hflipped = True
            # C, H, W
            bev_map = torch.flip(bev_map, [-1])

        targets = self.build_targets(labels, hflipped)

        metadatas = {
            'img_path': img_path,
            'hflipped': hflipped
        }

        return metadatas, bev_map, targets

    def get_image(self, idx):
        img_path = os.path.join(self.image_dir, '{:06d}.png'.format(idx))
        img = cv2.cvtColor(cv2.imread(img_path), cv2.COLOR_BGR2RGB)

        return img_path, img

    def get_calib(self, idx):
        calib_file = os.path.join(self.calib_dir, '{:06d}.txt'.format(idx))
        # assert os.path.isfile(calib_file)
        return Calibration(calib_file)

    def get_lidar(self, idx):
        lidar_file = os.path.join(self.lidar_dir, '{:06d}.bin'.format(idx))
        # assert os.path.isfile(lidar_file)
        return np.fromfile(lidar_file, dtype=np.float32).reshape(-1, 4)

    def get_pred(self, idx):
        pred_file = os.path.join(self.pred_dir, '{:06d}'.format(idx), '{:06d}_pred.obj'.format(idx))
        # assert os.path.isfile(calib_file)
        return ParseObj(pred_file)

    def get_label(self, idx):
        labels = []
        label_path = os.path.join(self.label_dir, '{:06d}.txt'.format(idx))
        for line in open(label_path, 'r'):
            line = line.rstrip()
            line_parts = line.split(' ')
            obj_name = line_parts[0]  # 'Car', 'Pedestrian', ...
            cat_id = int(cnf.CLASS_NAME_TO_ID[obj_name])
            if cat_id <= -99:  # ignore Tram and Misc
                continue
            truncated = int(float(line_parts[1]))  # truncated pixel ratio [0..1]
            occluded = int(line_parts[2])  # 0=visible, 1=partly occluded, 2=fully occluded, 3=unknown
            alpha = float(line_parts[3])  # object observation angle [-pi..pi]
            # xmin, ymin, xmax, ymax
            bbox = np.array([float(line_parts[4]), float(line_parts[5]), float(line_parts[6]), float(line_parts[7])])
            # height, width, length (h, w, l)
            h, w, l = float(line_parts[8]), float(line_parts[9]), float(line_parts[10])
            # location (x,y,z) in camera coord.
            x, y, z = float(line_parts[11]), float(line_parts[12]), float(line_parts[13])
            ry = float(line_parts[14])  # yaw angle (around Y-axis in camera coordinates) [-pi..pi]

            object_label = [cat_id, x, y, z, h, w, l, ry]
            labels.append(object_label)

        if len(labels) == 0:
            labels = np.zeros((1, 8), dtype=np.float32)
            has_labels = False
        else:
            labels = np.array(labels, dtype=np.float32)
            has_labels = True

        return labels, has_labels

    def build_targets(self, labels, hflipped):
        minX = cnf.boundary['minX']
        maxX = cnf.boundary['maxX']
        minY = cnf.boundary['minY']
        maxY = cnf.boundary['maxY']
        minZ = cnf.boundary['minZ']
        maxZ = cnf.boundary['maxZ']

        num_objects = min(len(labels), self.max_objects)
        hm_l, hm_w = self.hm_size

        hm_main_center = np.zeros((self.num_classes, hm_l, hm_w), dtype=np.float32)
        cen_offset = np.zeros((self.max_objects, 2), dtype=np.float32)
        direction = np.zeros((self.max_objects, 2), dtype=np.float32)
        z_coor = np.zeros((self.max_objects, 1), dtype=np.float32)
        dimension = np.zeros((self.max_objects, 3), dtype=np.float32)

        indices_center = np.zeros((self.max_objects), dtype=np.int64)
        obj_mask = np.zeros((self.max_objects), dtype=np.uint8)

        for k in range(num_objects):
            cls_id, x, y, z, h, w, l, yaw = labels[k]
            cls_id = int(cls_id)
            # Invert yaw angle
            yaw = -yaw
            if not ((minX <= x <= maxX) and (minY <= y <= maxY) and (minZ <= z <= maxZ)):
                continue
            if (h <= 0) or (w <= 0) or (l <= 0):
                continue

            bbox_l = l / cnf.bound_size_x * hm_l
            bbox_w = w / cnf.bound_size_y * hm_w
            radius = compute_radius((math.ceil(bbox_l), math.ceil(bbox_w)))
            radius = max(0, int(radius))

            center_y = (x - minX) / cnf.bound_size_x * hm_l  # x --> y (invert to 2D image space)
            center_x = (y - minY) / cnf.bound_size_y * hm_w  # y --> x
            center = np.array([center_x, center_y], dtype=np.float32)

            if hflipped:
                center[0] = hm_w - center[0] - 1

            center_int = center.astype(np.int32)
            if cls_id < 0:
                ignore_ids = [_ for _ in range(self.num_classes)] if cls_id == - 1 else [- cls_id - 2]
                # Consider to make mask ignore
                for cls_ig in ignore_ids:
                    gen_hm_radius(hm_main_center[cls_ig], center_int, radius)
                hm_main_center[ignore_ids, center_int[1], center_int[0]] = 0.9999
                continue

            # Generate heatmaps for main center
            gen_hm_radius(hm_main_center[cls_id], center, radius)
            # Index of the center
            indices_center[k] = center_int[1] * hm_w + center_int[0]

            # targets for center offset
            cen_offset[k] = center - center_int

            # targets for dimension
            dimension[k, 0] = h
            dimension[k, 1] = w
            dimension[k, 2] = l

            # targets for direction
            direction[k, 0] = math.sin(float(yaw))  # im
            direction[k, 1] = math.cos(float(yaw))  # re
            # im -->> -im
            if hflipped:
                direction[k, 0] = - direction[k, 0]

            # targets for depth
            z_coor[k] = z - minZ

            # Generate object masks
            obj_mask[k] = 1

        targets = {
            'hm_cen': hm_main_center,
            'cen_offset': cen_offset,
            'direction': direction,
            'z_coor': z_coor,
            'dim': dimension,
            'indices_center': indices_center,
            'obj_mask': obj_mask,
        }

        return targets

    def draw_img_with_label(self, index):
        sample_id = int(self.sample_id_list[index])
        preds = self.get_pred(sample_id)
        img_path, img_rgb = self.get_image(sample_id)
        lidarData = self.get_lidar(sample_id)
        lidarData = filter_fov_lidar(lidarData)
        calib = self.get_calib(sample_id)
        labels, has_labels = self.get_label(sample_id)
        if has_labels:
            labels[:, 1:] = transformation.camera_to_lidar_box(labels[:, 1:], calib.V2C, calib.R0, calib.P2)

        if self.lidar_aug:
            lidarData, labels[:, 1:] = self.lidar_aug(lidarData, labels[:, 1:])

        lidarData, labels = get_filtered_lidar(lidarData, cnf.boundary, labels)
        bev_map = makeBEVMap(lidarData, cnf.boundary)
        lidarData[:, 2] = lidarData[:, 2] -2.73
        fv_points = transformation.lidar_to_camera_point(lidarData[:, :3], calib.V2C, calib.R0)
        fv_points_2d = project_to_image(fv_points, calib.P2)
        fv_map = makeFVMap(fv_points_2d, img_rgb.shape)

        return bev_map, labels, img_rgb, img_path, fv_map, preds

def project_to_image(pts_3d, P):
    pts_3d_homo = np.concatenate([pts_3d, np.ones((pts_3d.shape[0], 1), dtype=np.float32)], axis=1)
    pts_2d = np.dot(P, pts_3d_homo.transpose(1, 0)).transpose(1, 0)
    pts_2d = pts_2d[:, :2] / pts_2d[:, 2:]

    return pts_2d.astype(np.int)

def ParseObj(obj_file):
    cache = []
    with open(obj_file, 'r') as f:
        lines = f.readlines()
        for line in lines:
            line = line.strip('\n')
            line = line.split(' ')
            cache.append(line)
    cache = np.array(cache)
    cache = cache[cache[:,0] == 'v']
    assert len(cache) % 8 == 0
    cache = cache[:,1:]
    cache = np.array(cache, dtype=np.float32)
    return cache.reshape(-1, 8, 3)


if __name__ == '__main__':
    from easydict import EasyDict as edict
    from transform_utils import OneOf, Random_Scaling, Random_Rotation, lidar_to_camera_box
    from data_process.visualize_utils import merge_rgb_to_bev, show_rgb_image_with_boxes, draw_box_3d
    from usr_config import mode

    configs = edict()
    configs.distributed = False  # For testing
    configs.pin_memory = False
    configs.num_samples = None
    configs.input_size = (608, 608)
    configs.hm_size = (152, 152)
    configs.max_objects = 50
    configs.num_classes = 3
    configs.output_width = 608
# 
    # configs.dataset_dir = os.path.join('../../', 'dataset', 'kitti')
    configs.dataset_dir = r'G:\KITTI_3D_new'
    configs.results_dir = os.path.join(configs.dataset_dir, 'results')

    lidar_aug = None

    out_cap = None

    dataset = KittiDataset(configs, mode='test', lidar_aug=lidar_aug, hflip_prob=0., num_samples=configs.num_samples)
    print('\n\nPress n to see the next sample >>> Press Esc to quit...')
    for idx in tqdm(range(len(dataset))):
        bev_map, labels, img_rgb, img_path, fv_map, preds = dataset.draw_img_with_label(idx)
        calib = Calibration(img_path.replace(".png", ".txt").replace("image_2", "calib"))
        bev_map = (bev_map.transpose(1, 2, 0) * 255).astype(np.uint8)
        bev_map = cv2.resize(bev_map, (cnf.BEV_HEIGHT, cnf.BEV_WIDTH))
        for box_idx, (cls_id, x, y, z, h, w, l, yaw) in enumerate(labels):
            # Draw rotated box
            yaw = -yaw
            y1 = int((x - cnf.boundary['minX']) / cnf.DISCRETIZATION)
            x1 = int((y - cnf.boundary['minY']) / cnf.DISCRETIZATION)
            w1 = int(w / cnf.DISCRETIZATION)
            l1 = int(l / cnf.DISCRETIZATION)

            drawRotatedBox(bev_map, x1, y1, w1, l1, yaw, cnf.colors[int(cls_id)])

        for pred in preds:
            # Draw rotated box
            bev_corners = np.zeros((4, 2), dtype=np.float32)
            bev_corners[0, 0] = pred[0][0]
            bev_corners[0, 1] = pred[0][1]

            bev_corners[1, 0] = pred[2][0]
            bev_corners[1, 1] = pred[2][1]

            bev_corners[2, 0] = pred[6][0]
            bev_corners[2, 1] = pred[6][1]

            bev_corners[3, 0] = pred[4][0]
            bev_corners[3, 1] = pred[4][1]

            bev_corners[:, 0] = (-bev_corners[:, 0] - cnf.boundary['minY']) / cnf.DISCRETIZATION
            bev_corners[:, 1] = (bev_corners[:, 1] - cnf.boundary['minX']) / cnf.DISCRETIZATION

            corners_int = bev_corners.reshape(-1, 1, 2).astype(int)
            cv2.polylines(bev_map, [corners_int], True, (0,255,0), 1)
            corners_int = bev_corners.reshape(-1, 2)
            cv2.line(bev_map, (int(round(corners_int[0, 0])), int(round(corners_int[0, 1]))),
                     (int(round(corners_int[3, 0])), int(round(corners_int[3, 1]))), (0, 255, 0), 1)
        # Rotate the bev_map
        bev_map = cv2.rotate(bev_map, cv2.ROTATE_180)


        labels[:, 1:] = lidar_to_camera_box(labels[:, 1:], calib.V2C, calib.R0, calib.P2)
        img_rgb = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        img_rgb = cv2.add(img_rgb, fv_map, dtype=cv2.CV_8UC3)
        img_rgb = show_rgb_image_with_boxes(img_rgb, labels, calib)
        preds = preds.reshape(-1,3)
        preds[:,[0,1]] = preds[:,[1,0]]
        preds = transformation.lidar_to_camera_point(preds, calib.V2C, calib.R0)
        preds[:,0] = -preds[:,0]
        preds_2d = project_to_image(preds, calib.P2).reshape(-1,8,2)

        for pred in preds_2d:
            img_rgb = draw_box_3d(img_rgb, pred, (0,255,0))

        out_img = merge_rgb_to_bev(img_rgb, bev_map, output_width=configs.output_width)
        if mode == 'step':
            cv2.imshow('bev_map', out_img)

            if cv2.waitKey(0) & 0xff == 27:
                break

        if mode == 'record':
            if out_cap is None:
                out_cap_h, out_cap_w = out_img.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*'MJPG')
                out_cap = cv2.VideoWriter(
                    os.path.join(configs.results_dir, '{}.avi'.format('gt')),
                    fourcc, 30, (out_cap_w, out_cap_h))

            out_cap.write(out_img)

    if out_cap:
        out_cap.release()
    cv2.destroyAllWindows()
