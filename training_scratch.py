import torch

from utils.ecoco_sequence_loader import *
from model.model import E2VIDRecurrent
from utils.train_utils import PreProcessOptions, RescalerOptions, UMSOptions, plot_training_data, training_loop
from utils.inference_utils import EventPreprocessor, IntensityRescaler, CropParameters, UnsharpMaskFilter
from utils.loading_utils import get_device
from utils.ecoco_dataset import ECOCO_Train_Dataset, ECOCO_Validation_Dataset
import lpips

if __name__ == "__main__":
    # ======================================================================================================================================================
    # Model definition
    config = {'recurrent_block_type': 'convlstm', 'num_bins': 5, 'skip_type': 'sum', 'num_encoders': 3,
              'base_num_channels': 32, 'num_residual_blocks': 2, 'norm': 'BN', 'use_upsample_conv': True}
    model = E2VIDRecurrent(config=config).cuda()

    # Event preprocessor
    options = PreProcessOptions()
    preprocessor = EventPreprocessor(options)
    options = RescalerOptions()
    rescaler = IntensityRescaler(options)
    options = UMSOptions()
    filt = UnsharpMaskFilter(options, 'cuda:0')

    # ignore the code above, they are just used for taking out the event tensor and model
    device = get_device(True)
    # DATA_DIR = '/home/richard/Q3/Deep_Learning/ruben-mr.github.io/data'
    torch.manual_seed(42)
    batch_size = 2
    shift = 8
    start_idx = 0

    # These ones are the ones to be changed
    num_epochs = 60
    seq_length = 8
    n_seq_usages = 1
    data_path = DATA_DIR

    train_dataset = ECOCO_Train_Dataset(sequence_length=seq_length, start_index=start_idx, shift=shift,
                                        n_shifts=n_seq_usages, path=data_path)
    val_dataset = ECOCO_Validation_Dataset(sequence_length=seq_length, start_index=start_idx, shift=shift,
                                           n_shifts=n_seq_usages, path=data_path)

    events, images, flows = train_dataset.__getitem__(0)
    height = events.shape[-2]
    width = events.shape[-1]
    crop = CropParameters(width, height, model.num_encoders)

    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = torch.utils.data.DataLoader(val_dataset, batch_size=batch_size, shuffle=True)

    if torch.cuda.is_available():
        reconstruction_loss_fn = lpips.LPIPS(net='vgg').cuda()
    else:
        reconstruction_loss_fn = lpips.LPIPS(net='vgg')

    train_losses, val_losses = training_loop(model, train_loader, val_loader, reconstruction_loss_fn,
                                             crop, preprocessor, rescaler, filt=filt, lr=0.0001, epoch=num_epochs)
    print(train_losses)
    print(val_losses)
    plot_training_data(train_losses, val_losses)
