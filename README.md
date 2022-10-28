# KITTI_Visualize

GitHub Link:[A convenient tool to visualize KITTI dataset.](https://github.com/SekiroRong/KITTI_Visualize)

## Feature

- [x] Support Kitti dataset

- [x] Support 2D/3D detection and segment mission

- [ ] Support other dataset

- [ ] Suport other mission

## Result

![output.gif](output.gif)

## Requirement

```
pip install  requirements.txt
```

The project is based on **carla-0.9.12**, which can be download here: [CARLA 0.9.12 Release | CARLA Simulator](http://carla.org/2021/08/02/release-0.9.12/)

And this version of carla seems require **python 3.7**.

## Get Start

All script you need to run is in the Usr folder, and you can set most of the parameter in config.py

### Generate Raw Data

1. Run the Carla Simulator First

2. Run the automatic_control.py

3. Then you should manually check the images which have been generated automatically in order to make the dataset clean, because the Carla Simulator makes some mistakes from time to time.

4. Run the kittiSynchronize.py to make sure that all parts of the dataset is generated synchronously.

### Turn Raw Data into Train Data(Kitti format)

1. Run the jpg2mp4.py

## Output Structure

The generator is designed to produce a kitti-like dataset for now.

## Contact

If you think this work is useful, please give me a star!  
If you find any errors or have any suggestions, please contact me (**Email:** `sekirorong@gmail.com`).  
Thank you!
